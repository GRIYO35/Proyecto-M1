"""Guardrail de salida: corre sobre el contrato ya validado, antes de devolverlo.

Revisa `answer` (lo ve el cliente) y `actions` (lo ve el agente):
1. Fuga del system prompt: si repite frases de las instrucciones -> bloquear.
2. Eco de un ataque: si la salida contiene frases de injection/jailbreak -> bloquear.
3. Links a dominios no permitidos -> se quitan y se marca.
4. Datos personales -> se enmascaran y se marca.
"""

import re

from src.esquemas import RespuestaTriage
from src.guardrails import BLOQUEAR, MARCAR, Decision, Motivo, buscar_patrones, cargar_politica, normalizar
from src.guardrails.pii import enmascarar_pii

_URL = re.compile(r"(?:https?://|www\.)[^\s)\"'>]+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|ar|info|xyz)(?:/[^\s)\"'>]*)?", re.IGNORECASE)
# Frases más cortas aparecen por casualidad o porque el prompt le pide al modelo que
# las use (ej. la acción "Revisar: posible intento de manipulación", 31 caracteres).
_LARGO_MINIMO_FRAGMENTO = 40


def fragmentos_del_prompt(sistema: str) -> list[str]:
    """Frases largas del system prompt; si alguna aparece literal en la salida, hubo fuga."""
    # El YAML parte las oraciones en varias líneas y les antepone viñetas o títulos:
    # se corta por fin de oración o de viñeta, y cada pedazo se vuelve a unir en una línea.
    pedazos = re.split(r"[.:;]|\n\s*(?:-|#+|\d+\.)\s", sistema)
    fragmentos = [normalizar(p).strip("-#*\"' ") for p in pedazos]
    return [f for f in fragmentos if len(f) >= _LARGO_MINIMO_FRAGMENTO and "{" not in f]


def _dominio_permitido(url: str) -> bool:
    dominio = re.sub(r"^(https?://)?(www\.)?", "", url.lower()).split("/")[0]
    return any(dominio == d or dominio.endswith("." + d) for d in cargar_politica().dominios_permitidos)


def _limpiar_texto(texto: str, decision: Decision) -> str:
    def quitar_url(coincidencia: re.Match) -> str:
        if _dominio_permitido(coincidencia.group()):
            return coincidencia.group()
        decision.agregar(MARCAR, Motivo("enlace_no_permitido", "La respuesta incluía un link a un dominio externo."))
        return "[enlace removido]"

    # Primero PII y después links: si no, el dominio de un email se toma como link.
    texto, pii = enmascarar_pii(texto)
    if pii:
        decision.agregar(MARCAR, Motivo("pii_en_salida", f"Se enmascararon datos personales: {dict(pii)}."))
    return _URL.sub(quitar_url, texto)


def revisar_salida(respuesta: RespuestaTriage, sistema: str) -> tuple[RespuestaTriage, Decision]:
    decision = Decision()
    textos = [respuesta.answer, *respuesta.actions]
    salida_normalizada = normalizar(" ".join(textos))

    if any(f in salida_normalizada for f in fragmentos_del_prompt(sistema)):
        decision.agregar(BLOQUEAR, Motivo("fuga_prompt", "La salida reproduce partes de las instrucciones internas."))

    eco = buscar_patrones(" ".join(textos), solo_codigos=cargar_politica().reglas_eco_en_salida)
    for motivo in eco.motivos:
        decision.agregar(BLOQUEAR, Motivo(f"eco_{motivo.codigo}", "La salida repite lenguaje de un ataque."))

    if decision.accion == BLOQUEAR:
        return respuesta, decision  # quien llama la reemplaza por la respuesta segura

    limpia = respuesta.model_copy(
        update={
            "answer": _limpiar_texto(respuesta.answer, decision),
            "actions": [_limpiar_texto(a, decision) for a in respuesta.actions],
        }
    )
    return limpia, decision
