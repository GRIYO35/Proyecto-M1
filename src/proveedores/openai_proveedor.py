import os

import openai

from src.config import ConfigProveedor
from src.proveedores.base import ProveedorLLM, ResultadoAPI
from src.proveedores.errores import (
    ErrorAutenticacion,
    ErrorConfiguracion,
    ErrorDefinitivo,
    ErrorTransitorio,
    leer_retry_after,
)


NOMBRE_TOOL = "clasificar_consulta"


class ProveedorOpenAI(ProveedorLLM):
    nombre = "openai"

    def __init__(self, config: ConfigProveedor):
        super().__init__(config)
        if not os.getenv("OPENAI_API_KEY"):
            raise ErrorConfiguracion("Falta OPENAI_API_KEY en el .env")
        # max_retries=0: los reintentos los maneja ProveedorLLM, igual para todos los proveedores.
        self._cliente = openai.OpenAI(max_retries=0, timeout=self.config.timeout_s)

    def _llamar_api(self, sistema: str, mensaje_usuario: str, schema: dict) -> ResultadoAPI:
        try:
            respuesta = self._cliente.chat.completions.create(
                model=self.config.modelo,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperatura,
                # Tool calling forzado: el modelo tiene que "llamar" a la función con argumentos
                # que respeten el schema, en lugar de responder texto libre.
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": NOMBRE_TOOL,
                            "description": "Registra la clasificación y la respuesta propuesta para el ticket.",
                            "parameters": schema,
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": NOMBRE_TOOL}},
                messages=[
                    {"role": "system", "content": sistema},
                    {"role": "user", "content": mensaje_usuario},
                ],
            )
        except openai.RateLimitError as error:
            raise ErrorTransitorio(f"rate limit (429): {error}", leer_retry_after(error.response.headers)) from error
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise ErrorAutenticacion(f"credencial rechazada ({error.status_code}): {error.message}") from error
        except openai.APIStatusError as error:
            if error.status_code >= 500:
                raise ErrorTransitorio(
                    f"error del servidor ({error.status_code})", leer_retry_after(error.response.headers)
                ) from error
            raise ErrorDefinitivo(f"request rechazada ({error.status_code}): {error.message}") from error
        except openai.APIConnectionError as error:  # incluye timeouts
            raise ErrorTransitorio(f"falla de conexión: {error}") from error

        eleccion = respuesta.choices[0]
        if eleccion.finish_reason == "content_filter":
            raise ErrorDefinitivo("el proveedor filtró la respuesta (finish_reason=content_filter)")
        # finish_reason == "length": los argumentos llegan truncados y no se parsean;
        # la capa de contrato reintenta avisándole al modelo (ver triage.py).
        llamadas = eleccion.message.tool_calls or []
        # OpenAI entrega los argumentos como string; se normalizan a dict en ProveedorLLM.
        argumentos = llamadas[0].function.arguments if llamadas else (eleccion.message.content or "")

        return ResultadoAPI(
            argumentos,
            respuesta.usage.prompt_tokens,
            respuesta.usage.completion_tokens,
        )
