from src.config import cargar_config_proveedor
from src.proveedores.anthropic_proveedor import ProveedorAnthropic
from src.proveedores.base import ProveedorLLM, RespuestaLLM
from src.proveedores.errores import (
    ErrorConfiguracion,
    ErrorDefinitivo,
    ErrorProveedor,
    ProveedorNoDisponible,
)
from src.proveedores.openai_proveedor import ProveedorOpenAI

_PROVEEDORES: dict[str, type[ProveedorLLM]] = {
    ProveedorAnthropic.nombre: ProveedorAnthropic,
    ProveedorOpenAI.nombre: ProveedorOpenAI,
}


def crear_proveedor(nombre: str, config: str | None = None) -> ProveedorLLM:
    """`config` permite usar otra entrada de proveedores.yaml con el mismo cliente
    (por ejemplo, el juez de la suite de evaluación)."""
    if nombre not in _PROVEEDORES:
        raise ErrorConfiguracion(f"Proveedor desconocido '{nombre}'. Disponibles: {', '.join(_PROVEEDORES)}")
    return _PROVEEDORES[nombre](cargar_config_proveedor(config or nombre))


__all__ = [
    "ErrorConfiguracion",
    "ErrorDefinitivo",
    "ErrorProveedor",
    "ProveedorLLM",
    "ProveedorNoDisponible",
    "RespuestaLLM",
    "crear_proveedor",
]
