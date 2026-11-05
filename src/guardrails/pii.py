"""Detección y enmascarado de datos personales (PII).

Se aplica en dos puntos:
  - entrada: antes de mandar la consulta al LLM (minimización: el modelo no
    necesita el mail ni la tarjeta del cliente para clasificar el ticket).
  - salida: por si el modelo igual devuelve algo con forma de dato personal.

El agente humano sigue viendo el ticket original en su sistema de soporte; lo
que se enmascara es lo que viaja al proveedor de LLM y lo que vuelve de él.
"""

import re
from collections import Counter

# El orden importa: lo más específico primero, para que el patrón de teléfono
# no se "coma" una tarjeta o un CUIT.
_TARJETA = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_CBU = re.compile(r"\b\d{22}\b")
_CUIT = re.compile(r"\b(?:20|23|24|27|30|33|34)-?\d{8}-?\d\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_TELEFONO = re.compile(r"(?<![\w$])\+?\d[\d\s-]{7,}\d\b")


def _luhn_valido(numero: str) -> bool:
    """Verificación de Luhn: evita enmascarar como tarjeta cualquier número largo."""
    digitos = [int(c) for c in re.sub(r"\D", "", numero)]
    if not 13 <= len(digitos) <= 19:
        return False
    suma = 0
    for i, d in enumerate(reversed(digitos)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        suma += d
    return suma % 10 == 0


def enmascarar_pii(texto: str) -> tuple[str, Counter]:
    """Devuelve (texto enmascarado, cantidad de datos enmascarados por tipo)."""
    encontrados: Counter = Counter()

    def reemplazar(tipo: str, marcador: str):
        def _sub(coincidencia: re.Match) -> str:
            encontrados[tipo] += 1
            return marcador

        return _sub

    def tarjeta(coincidencia: re.Match) -> str:
        if _luhn_valido(coincidencia.group()):
            encontrados["tarjeta"] += 1
            return "[tarjeta]"
        return coincidencia.group()

    texto = _TARJETA.sub(tarjeta, texto)
    texto = _CBU.sub(reemplazar("cbu", "[cbu]"), texto)
    texto = _CUIT.sub(reemplazar("cuit", "[cuit]"), texto)
    texto = _EMAIL.sub(reemplazar("email", "[email]"), texto)

    def telefono(coincidencia: re.Match) -> str:
        # Un teléfono tiene entre 8 y 13 dígitos; un número de pedido de 16 no lo es.
        if 8 <= len(re.sub(r"\D", "", coincidencia.group())) <= 13:
            encontrados["telefono"] += 1
            return "[telefono]"
        return coincidencia.group()

    texto = _TELEFONO.sub(telefono, texto)
    return texto, encontrados
