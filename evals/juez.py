"""LLM-as-judge para el razonamiento de los prompts chain-of-thought (v4_cot).

La categoría se evalúa con exact match: es una salida cerrada. El razonamiento es
texto libre, así que no se puede comparar con ==; se juzga contra un criterio
escrito de antemano en el dataset (`criterio_razonamiento`).

Para que el juez sea confiable y no "otro número más":
  - Es un modelo distinto y más capaz que el evaluado (config `juez_anthropic`).
  - El veredicto es una salida estructurada cerrada (si/no), validada con Pydantic.
  - Se puede juzgar varias veces y decidir por mayoría (ver config/evals.yaml).
  - Antes de usarlo se valida contra casos con veredicto humano:

        python -m evals.juez                 # calibración contra evals/juez_calibracion.jsonl
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Ejecutado como script (`python evals/juez.py`): se agrega la raíz del proyecto al path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

from src.esquemas import schema_para_api
from src.proveedores import ProveedorLLM, crear_proveedor

CARPETA_EVALS = Path(__file__).resolve().parent
RUTA_CONFIG_EVALS = CARPETA_EVALS.parent / "config" / "evals.yaml"
RUTA_CALIBRACION = CARPETA_EVALS / "juez_calibracion.jsonl"

# Prompt del juez versionado en YAML, igual que los de triage (no se edita: se crea otra versión).
RUTA_PROMPT_JUEZ = CARPETA_EVALS.parent / "prompts" / "juez" / "v1_juez.yaml"
JUEZ_SISTEMA = yaml.safe_load(RUTA_PROMPT_JUEZ.read_text(encoding="utf-8"))["sistema"]


class VeredictoJuez(BaseModel):
    """`motivo` va antes que `veredicto` para que el juez decida después de justificar."""

    model_config = ConfigDict(extra="forbid")
    motivo: str
    veredicto: Literal["si", "no"]


@dataclass
class Juicio:
    veredicto: str  # "si" | "no" | "error"
    votos_si: int
    votos: int
    motivos: list[str] = field(default_factory=list)
    costo_usd: float = 0.0


def cargar_config_evals() -> dict:
    return yaml.safe_load(RUTA_CONFIG_EVALS.read_text(encoding="utf-8"))


def crear_juez(uso: Literal["suite", "calibracion"]) -> tuple[ProveedorLLM, int]:
    """Usa el juez configurado; si falta su API key, el alternativo (así el repo corre
    con la key de cualquiera de los dos proveedores)."""
    config = cargar_config_evals()["juez"]
    elegido = config
    if not os.getenv(f"{config['proveedor'].upper()}_API_KEY") and config.get("alternativa"):
        elegido = config["alternativa"]
    proveedor = crear_proveedor(elegido["proveedor"], config=elegido["config"])
    return proveedor, config[f"repeticiones_{uso}"]


def armar_mensaje(consulta: str, criterio: str, razonamiento: str) -> str:
    return (
        f"<consulta>\n{consulta}\n</consulta>\n\n"
        f"<criterio_esperado>\n{criterio}\n</criterio_esperado>\n\n"
        f"<razonamiento_del_modelo>\n{razonamiento}\n</razonamiento_del_modelo>"
    )


def juzgar(juez: ProveedorLLM, consulta: str, criterio: str, razonamiento: str | None, repeticiones: int) -> Juicio:
    """Juzga `repeticiones` veces y decide por mayoría (empate = "no", el juez es estricto)."""
    if not razonamiento:
        return Juicio("no", 0, 0, ["el modelo no devolvió razonamiento"])
    schema = schema_para_api(VeredictoJuez)
    mensaje = armar_mensaje(consulta, criterio, razonamiento)
    votos: list[str] = []
    motivos: list[str] = []
    costo = 0.0
    for _ in range(repeticiones):
        try:
            respuesta = juez.completar(JUEZ_SISTEMA, mensaje, schema)
            costo += respuesta.costo_usd
            voto = VeredictoJuez.model_validate(respuesta.datos)
        except Exception as error:  # un juicio fallido (API o formato) no corta la suite
            motivos.append(f"error del juez: {type(error).__name__}")
            continue
        votos.append(voto.veredicto)
        motivos.append(voto.motivo)
    if not votos:
        return Juicio("error", 0, 0, motivos, costo)
    votos_si = votos.count("si")
    return Juicio("si" if votos_si > len(votos) / 2 else "no", votos_si, len(votos), motivos, costo)


def calibrar() -> None:
    """Compara el juez contra veredictos humanos. Si no coinciden, el problema es el juez."""
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser(description="Valida el juez contra casos con veredicto humano.")
    parser.add_argument("--casos", type=Path, default=RUTA_CALIBRACION)
    argumentos = parser.parse_args()

    umbral = cargar_config_evals()["umbral_minimo_acuerdo_juez"]
    juez, repeticiones = crear_juez("calibracion")
    casos = [json.loads(l) for l in argumentos.casos.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"Juez: {juez.config.modelo} | {repeticiones} juicios por caso | {len(casos)} casos de calibración\n")

    acuerdos, costo, unanimes = 0, 0.0, 0
    for caso in casos:
        juicio = juzgar(juez, caso["consulta"], caso["criterio_razonamiento"], caso["razonamiento"], repeticiones)
        costo += juicio.costo_usd
        acuerdo = juicio.veredicto == caso["veredicto_humano"]
        acuerdos += acuerdo
        unanimes += juicio.votos_si in (0, juicio.votos)
        marca = "✓" if acuerdo else "✗"
        print(
            f"{marca} {caso['id']}: humano={caso['veredicto_humano']} juez={juicio.veredicto} "
            f"({juicio.votos_si}/{juicio.votos} si) — {caso['tipo']}"
        )
        if not acuerdo:
            print(f"    motivo del juez: {juicio.motivos[0]}")

    tasa = acuerdos / len(casos)
    print(
        f"\nAcuerdo juez-humano: {acuerdos}/{len(casos)} ({tasa:.0%}) | umbral {umbral:.0%} | "
        f"juicios unánimes: {unanimes}/{len(casos)} | costo USD {costo:.4f}"
    )
    if tasa < umbral:
        print("El juez NO pasa la calibración: revisar prompts/juez/ o el modelo antes de confiar en sus puntajes.")
        sys.exit(4)
    print("El juez pasa la calibración.")


if __name__ == "__main__":
    calibrar()
