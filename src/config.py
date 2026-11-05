"""Lectura de config/proveedores.yaml."""

from dataclasses import dataclass
from pathlib import Path

import yaml

RUTA_CONFIG_PROVEEDORES = Path(__file__).resolve().parent.parent / "config" / "proveedores.yaml"


@dataclass(frozen=True)
class ConfigProveedor:
    modelo: str
    max_tokens: int
    timeout_s: float
    precio_entrada_usd_por_millon: float
    precio_salida_usd_por_millon: float
    # None = no se envía (Haiku 5.5 no acepta temperature).
    temperatura: float | None = None
    # Sólo Anthropic: nivel de esfuerzo de razonamiento (low | medium | high).
    effort: str = "low"

    def calcular_costo(self, tokens_entrada: int, tokens_salida: int) -> float:
        """Costo real a partir del usage que devuelve la API (no de una estimación)."""
        return (
            tokens_entrada / 1_000_000 * self.precio_entrada_usd_por_millon
            + tokens_salida / 1_000_000 * self.precio_salida_usd_por_millon
        )


def cargar_config_proveedor(nombre: str) -> ConfigProveedor:
    """`nombre` es una entrada de proveedores.yaml (p. ej. "anthropic" o "juez_anthropic")."""
    datos = yaml.safe_load(RUTA_CONFIG_PROVEEDORES.read_text(encoding="utf-8"))
    if nombre not in datos:
        raise ValueError(f"Proveedor '{nombre}' no configurado. Disponibles: {', '.join(datos)}")
    return ConfigProveedor(**{k: v for k, v in datos[nombre].items() if k != "proveedor"})
