import os

import anthropic

from src.config import ConfigProveedor
from src.proveedores.base import ProveedorLLM, ResultadoAPI
from src.proveedores.errores import (
    ErrorConfiguracion,
    ErrorDefinitivo,
    ErrorTransitorio,
    leer_retry_after,
)


class ProveedorAnthropic(ProveedorLLM):
    nombre = "anthropic"

    def __init__(self, config: ConfigProveedor):
        super().__init__(config)
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ErrorConfiguracion("Falta ANTHROPIC_API_KEY en el .env")
        # max_retries=0: los reintentos los maneja ProveedorLLM, igual para todos los proveedores.
        self._cliente = anthropic.Anthropic(max_retries=0, timeout=self.config.timeout_s)

    def _llamar_api(self, sistema: str, mensaje_usuario: str, schema: dict) -> ResultadoAPI:
        try:
            respuesta = self._cliente.messages.create(
                model=self.config.modelo,
                max_tokens=self.config.max_tokens,
                system=sistema,
                output_config={
                    # Haiku 5.5 no acepta temperature (400 con valores distintos del default).
                    # El control equivalente es effort: bajo para clasificar = menos tokens y latencia
                    # (configurable en proveedores.yaml; el juez de evals usa medium).
                    "effort": self.config.effort,
                    # Structured outputs: la API restringe la generación al schema. Se usa en lugar
                    # de tool calling forzado porque Haiku 5.5 piensa por defecto, y el forzado de
                    # herramientas (tool_choice tool/any) no es compatible con el razonamiento.
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": mensaje_usuario}],
            )
        except anthropic.RateLimitError as error:
            raise ErrorTransitorio(f"rate limit (429): {error}", leer_retry_after(error.response.headers)) from error
        except anthropic.APIStatusError as error:
            if error.status_code >= 500:
                raise ErrorTransitorio(
                    f"error del servidor ({error.status_code})", leer_retry_after(error.response.headers)
                ) from error
            raise ErrorDefinitivo(f"request rechazada ({error.status_code}): {error.message}") from error
        except anthropic.APIConnectionError as error:  # incluye timeouts
            raise ErrorTransitorio(f"falla de conexión: {error}") from error

        if respuesta.stop_reason == "refusal":
            raise ErrorDefinitivo("el modelo rechazó la consulta (stop_reason=refusal)")
        # Si se cortó por max_tokens, el JSON llega incompleto: no se parsea y la capa de
        # contrato reintenta avisándole al modelo (ver triage.py).
        # La respuesta puede traer bloques de razonamiento; sólo nos interesa el texto.
        texto = "".join(bloque.text for bloque in respuesta.content if bloque.type == "text")
        return ResultadoAPI(texto, respuesta.usage.input_tokens, respuesta.usage.output_tokens)
