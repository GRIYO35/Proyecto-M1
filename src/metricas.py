"""Registros append-only (una línea JSON por evento).

- data/metrics.jsonl:   una línea por llamada al LLM (tokens, latencia, costo).
- data/seguridad.jsonl: una línea por consulta con la decisión de los guardrails.

Ninguno guarda el texto del cliente ni la respuesta: en el log de seguridad
queda sólo un hash con salt, que permite correlacionar sin exponer contenido.
Las dos líneas de una misma consulta comparten `request_id`.
"""

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.guardrails import mas_severa
from src.proveedores import RespuestaLLM

CARPETA_DATOS = Path(__file__).resolve().parent.parent / "data"
RUTA_METRICAS = CARPETA_DATOS / "metrics.jsonl"
RUTA_SEGURIDAD = CARPETA_DATOS / "seguridad.jsonl"
_CANDADO = threading.Lock()  # el runner de evals llama en paralelo

# Etiqueta de retención según la decisión: los eventos benignos se pueden borrar antes.
_RETENCION = {"permitir": "limpio_30d", "marcar": "marcado_90d", "bloquear": "bloqueado_365d"}


def _escribir(ruta: Path, registro: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with _CANDADO, ruta.open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def hash_texto(texto: str) -> str:
    """SHA-256 con salt: el mismo texto da el mismo hash, pero no se puede revertir."""
    # Vacío o ausente (p. ej. recién copiado de .env.example) = salt por defecto.
    salt = os.getenv("LOG_SALT") or "cambiar-en-produccion"
    return hashlib.sha256((salt + texto).encode("utf-8")).hexdigest()[:16]


def registrar_exito(
    respuesta: RespuestaLLM, version_prompt: str, intento_contrato: int = 1, request_id: str | None = None
) -> None:
    _escribir(
        RUTA_METRICAS,
        {
            "timestamp": _ahora(),
            "request_id": request_id,
            "proveedor": respuesta.proveedor,
            "modelo": respuesta.modelo,
            "prompt_version": version_prompt,
            "prompt_tokens": respuesta.prompt_tokens,
            "completion_tokens": respuesta.completion_tokens,
            "total_tokens": respuesta.total_tokens,
            "latencia_s": round(respuesta.latencia_s, 3),
            "costo_usd": round(respuesta.costo_usd, 8),
            "intentos": respuesta.intentos,  # reintentos por errores de red / rate limit
            "intento_contrato": intento_contrato,  # >1 = reintento por salida que no validó
            "exito": True,
        },
    )


def registrar_fallo(proveedor: str, version_prompt: str, error: Exception, request_id: str | None = None) -> None:
    """Las llamadas fallidas también se registran: sin esto, una caída del proveedor no deja rastro."""
    _escribir(
        RUTA_METRICAS,
        {
            "timestamp": _ahora(),
            "request_id": request_id,
            "proveedor": proveedor,
            "prompt_version": version_prompt,
            "exito": False,
            "error": f"{type(error).__name__}: {error}",
        },
    )


def registrar_seguridad(
    request_id: str,
    consulta: str,
    version_politica: str,
    version_prompt: str,
    accion_entrada: str,
    accion_salida: str | None,
    motivos: list[str],
    pii_enmascarada: dict,
) -> None:
    accion_final = mas_severa(accion_entrada, accion_salida)
    _escribir(
        RUTA_SEGURIDAD,
        {
            "timestamp": _ahora(),
            "request_id": request_id,
            "input_hash": hash_texto(consulta),
            "politica_version": version_politica,
            "prompt_version": version_prompt,
            "accion_entrada": accion_entrada,
            "accion_salida": accion_salida,  # None si se bloqueó en la entrada y no hubo salida
            "accion_final": accion_final,
            "motivos": motivos,  # sólo códigos, nunca texto del cliente
            "pii_enmascarada": pii_enmascarada,  # cantidades por tipo, no los valores
            "retention_tag": _RETENCION[accion_final],
        },
    )
