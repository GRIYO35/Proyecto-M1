"""Errores comunes a todos los proveedores.

Cada proveedor traduce las excepciones de su SDK a estas clases, así la lógica
de reintentos y de fallback no depende de ningún SDK en particular.
"""


class ErrorProveedor(Exception):
    """Error base de una llamada a un proveedor de LLM."""


class ErrorConfiguracion(ErrorProveedor):
    """Falta una credencial o la configuración es inválida. No se reintenta."""


class ErrorTransitorio(ErrorProveedor):
    """Rate limit (429), error del servidor (5xx), timeout o falla de red. Se reintenta.

    `espera_sugerida_s` es el valor del header Retry-After, si el proveedor lo mandó.
    """

    def __init__(self, mensaje: str, espera_sugerida_s: float | None = None):
        super().__init__(mensaje)
        self.espera_sugerida_s = espera_sugerida_s


class ErrorDefinitivo(ErrorProveedor):
    """Request inválida (4xx), rechazo del modelo o respuesta truncada. Reintentar no ayuda."""


class ProveedorNoDisponible(ErrorProveedor):
    """Se agotaron los reintentos ante errores transitorios. Habilita el fallback."""


class ErrorAutenticacion(ProveedorNoDisponible):
    """API key inválida o sin permisos (401/403). No se reintenta, pero habilita el fallback:
    es un problema de ese proveedor, no de la consulta, así que otro puede responderla."""


def leer_retry_after(headers) -> float | None:
    """Devuelve los segundos del header Retry-After, o None si no viene o no es numérico."""
    if headers is None:
        return None
    valor = headers.get("retry-after")
    try:
        return float(valor) if valor is not None else None
    except ValueError:
        # Retry-After también puede venir como fecha HTTP; en ese caso usamos el backoff propio.
        return None
