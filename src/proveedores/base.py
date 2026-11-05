"""Interfaz común a todos los proveedores de LLM.

La lógica de negocio sólo conoce `ProveedorLLM` y `RespuestaLLM`; cada SDK
queda encapsulado en su subclase. Los reintentos viven acá, una sola vez, y no
en cada SDK: por eso los clientes se crean con `max_retries=0`.
"""

import json
import random
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.config import ConfigProveedor
from src.proveedores.errores import ErrorTransitorio, ProveedorNoDisponible

MAX_INTENTOS = 4  # 1 intento + 3 reintentos
BACKOFF_BASE_S = 1.0  # esperas de 1, 2, 4 s (+ jitter)
ESPERA_MAXIMA_S = 30.0  # tope, incluso si Retry-After pide más: es una CLI interactiva


@dataclass(frozen=True)
class ResultadoAPI:
    """Lo mínimo que cada proveedor tiene que devolver, leído del `usage` real de la API.

    `texto` es la salida cruda: un JSON en texto (Anthropic, structured outputs) o los
    argumentos de la tool call (OpenAI, que también los entrega como string).
    """

    texto: str
    tokens_entrada: int
    tokens_salida: int


def parsear_json(texto: str) -> dict | None:
    """Normaliza la salida a dict. None si llegó truncada o no es un objeto JSON."""
    try:
        datos = json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        return None
    return datos if isinstance(datos, dict) else None


@dataclass(frozen=True)
class RespuestaLLM:
    texto: str
    datos: dict | None  # salida ya parseada; None si no era JSON válido
    tokens_entrada: int
    tokens_salida: int
    latencia_s: float
    costo_usd: float
    intentos: int
    proveedor: str
    modelo: str

    @property
    def tokens_total(self) -> int:
        return self.tokens_entrada + self.tokens_salida


def calcular_espera(intento: int, espera_sugerida_s: float | None) -> float:
    """Retry-After tiene prioridad: el proveedor conoce su carga mejor que nosotros.

    Si no viene, backoff exponencial con jitter para no reintentar todos a la vez.
    """
    if espera_sugerida_s is not None:
        return min(espera_sugerida_s, ESPERA_MAXIMA_S)
    return min(BACKOFF_BASE_S * (2**intento) + random.uniform(0, 1), ESPERA_MAXIMA_S)


class ProveedorLLM(ABC):
    nombre: str

    def __init__(self, config: ConfigProveedor):
        self.config = config

    def completar(self, sistema: str, mensaje_usuario: str, schema: dict) -> RespuestaLLM:
        inicio = time.perf_counter()
        for intento in range(MAX_INTENTOS):
            try:
                resultado = self._llamar_api(sistema, mensaje_usuario, schema)
                break
            except ErrorTransitorio as error:
                if intento == MAX_INTENTOS - 1:
                    raise ProveedorNoDisponible(
                        f"{self.nombre}: falló {MAX_INTENTOS} veces seguidas. Último error: {error}"
                    ) from error
                espera = calcular_espera(intento, error.espera_sugerida_s)
                # stderr para no ensuciar el JSON de stdout, que puede consumir otro sistema.
                print(
                    f"[{self.nombre}] intento {intento + 1}/{MAX_INTENTOS} falló ({error}); "
                    f"reintento en {espera:.1f}s",
                    file=sys.stderr,
                )
                time.sleep(espera)

        return RespuestaLLM(
            texto=resultado.texto,
            datos=parsear_json(resultado.texto),
            tokens_entrada=resultado.tokens_entrada,
            tokens_salida=resultado.tokens_salida,
            # Latencia total que percibe el usuario, incluyendo las esperas entre reintentos.
            latencia_s=time.perf_counter() - inicio,
            costo_usd=self.config.calcular_costo(resultado.tokens_entrada, resultado.tokens_salida),
            intentos=intento + 1,
            proveedor=self.nombre,
            modelo=self.config.modelo,
        )

    @abstractmethod
    def _llamar_api(self, sistema: str, mensaje_usuario: str, schema: dict) -> ResultadoAPI:
        """Hace una sola llamada al SDK, forzando que la salida respete `schema`.

        Debe traducir los errores del SDK a `ErrorTransitorio` (se reintenta) o
        `ErrorDefinitivo` (no se reintenta).
        """
