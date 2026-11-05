"""Carga de prompts versionados desde prompts/*.yaml.

Separación plantilla / variables: el YAML se versiona; los valores que se
inyectan en cada llamada no. Variables disponibles:
    {categorias}  en `sistema`: lista de categorías válidas, derivada de esquemas.CATEGORIAS
    {consulta}    en `usuario`: el texto del cliente
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.esquemas import CATEGORIAS

CARPETA_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def _categorias_como_texto() -> str:
    return "\n".join(f"- {nombre}: {descripcion}" for nombre, descripcion in CATEGORIAS.items())


@dataclass(frozen=True)
class Prompt:
    version: str
    tecnica: str
    descripcion: str
    plantilla_sistema: str
    plantilla_usuario: str
    # True = el modelo escribe un campo `razonamiento` antes de clasificar (chain-of-thought).
    razonamiento: bool = False

    @property
    def sistema(self) -> str:
        # replace y no format: el prompt puede contener llaves de ejemplos JSON.
        return self.plantilla_sistema.replace("{categorias}", _categorias_como_texto())

    def armar_mensaje_usuario(self, consulta: str) -> str:
        return self.plantilla_usuario.replace("{consulta}", consulta)


def versiones_disponibles() -> list[str]:
    return sorted(p.stem for p in CARPETA_PROMPTS.glob("*.yaml"))


def cargar_prompt(version: str) -> Prompt:
    ruta = CARPETA_PROMPTS / f"{version}.yaml"
    if not ruta.exists():
        raise ValueError(f"No existe el prompt '{version}'. Disponibles: {', '.join(versiones_disponibles())}")

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    return Prompt(
        version=datos["version"],
        tecnica=datos["tecnica"],
        descripcion=datos["descripcion"].strip(),
        plantilla_sistema=datos["sistema"],
        plantilla_usuario=datos["usuario"],
        razonamiento=datos.get("razonamiento", False),
    )
