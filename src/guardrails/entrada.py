"""Guardrail de entrada: corre antes de construir el prompt.

1. Detecta patrones conocidos de injection / jailbreak (config/guardrails.yaml).
2. Enmascara datos personales (el LLM no los necesita para clasificar).
3. Neutraliza etiquetas que imitan la estructura del prompt, para que el cliente
   no pueda "cerrar" el bloque <consulta> y escribir fuera del canal de datos.
"""

import re
from collections import Counter
from dataclasses import dataclass

from src.guardrails import Decision, buscar_patrones
from src.guardrails.pii import enmascarar_pii

_ETIQUETAS = re.compile(r"<\s*/?\s*(consulta|sistema|system|reglas|instrucciones)\s*>", re.IGNORECASE)


@dataclass
class ResultadoEntrada:
    decision: Decision
    texto_para_llm: str  # lo único que viaja al proveedor
    pii: Counter  # cuántos datos personales se enmascararon, por tipo


def revisar_entrada(texto: str) -> ResultadoEntrada:
    decision = buscar_patrones(texto)
    texto_seguro, pii = enmascarar_pii(texto)
    texto_seguro = _ETIQUETAS.sub("[etiqueta]", texto_seguro)
    return ResultadoEntrada(decision=decision, texto_para_llm=texto_seguro, pii=pii)
