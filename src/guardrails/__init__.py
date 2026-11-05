"""Guardrails del servicio de triage: controles de entrada y de salida.

Tres acciones posibles, de menor a mayor severidad:
    permitir -> sigue el flujo normal.
    marcar   -> sigue, pero se avisa al agente humano y se baja la confianza.
    bloquear -> no se usa la salida del modelo; se devuelve una respuesta segura.

La política (patrones, dominios permitidos, mensajes) vive en config/guardrails.yaml.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

RUTA_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "guardrails.yaml"

PERMITIR, MARCAR, BLOQUEAR = "permitir", "marcar", "bloquear"
_ORDEN = {PERMITIR: 0, MARCAR: 1, BLOQUEAR: 2}
_ACCION_POR_SEVERIDAD = {"alta": BLOQUEAR, "media": MARCAR}


def mas_severa(*acciones: str | None) -> str:
    """bloquear > marcar > permitir. Ignora los None (etapa que no corrió)."""
    return max((a for a in acciones if a), key=_ORDEN.__getitem__, default=PERMITIR)


@dataclass(frozen=True)
class Motivo:
    codigo: str
    descripcion: str


@dataclass
class Decision:
    accion: str = PERMITIR
    motivos: list[Motivo] = field(default_factory=list)

    def agregar(self, accion: str, motivo: Motivo) -> None:
        """La acción final es la más severa de todas las que se dispararon."""
        if _ORDEN[accion] > _ORDEN[self.accion]:
            self.accion = accion
        if motivo not in self.motivos:
            self.motivos.append(motivo)

    @property
    def codigos(self) -> list[str]:
        return [m.codigo for m in self.motivos]


@dataclass(frozen=True)
class ReglaPatron:
    codigo: str
    descripcion: str
    accion: str
    patrones: tuple[re.Pattern, ...]


@dataclass(frozen=True)
class Politica:
    version: str
    reglas: tuple[ReglaPatron, ...]
    dominios_permitidos: tuple[str, ...]
    respuesta_bloqueo: str
    reglas_eco_en_salida: tuple[str, ...]


@lru_cache(maxsize=1)
def cargar_politica() -> Politica:
    datos = yaml.safe_load(RUTA_CONFIG.read_text(encoding="utf-8"))
    reglas = tuple(
        ReglaPatron(
            codigo=r["codigo"],
            descripcion=r["descripcion"],
            accion=_ACCION_POR_SEVERIDAD[r["severidad"]],
            patrones=tuple(re.compile(p) for p in r["patrones"]),
        )
        for r in datos["patrones_entrada"]
    )
    return Politica(
        version=str(datos["version_politica"]),
        reglas=reglas,
        dominios_permitidos=tuple(datos["dominios_permitidos"]),
        respuesta_bloqueo=datos["respuesta_bloqueo"].strip(),
        reglas_eco_en_salida=tuple(datos["reglas_eco_en_salida"]),
    )


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios simples: 'Ignorá  TUS' -> 'ignora tus'."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", sin_tildes.lower()).strip()


def buscar_patrones(texto: str, solo_codigos: tuple[str, ...] | None = None) -> Decision:
    """Evalúa el texto contra las reglas de la política (todas, o sólo las de `solo_codigos`)."""
    decision = Decision()
    normalizado = normalizar(texto)
    for regla in cargar_politica().reglas:
        if solo_codigos is not None and regla.codigo not in solo_codigos:
            continue
        if any(p.search(normalizado) for p in regla.patrones):
            decision.agregar(regla.accion, Motivo(regla.codigo, regla.descripcion))
    return decision
