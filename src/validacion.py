"""Validación de la salida del modelo contra el contrato de Pydantic.

El modo JSON / structured outputs garantiza sintaxis y forma básica; Pydantic
garantiza estructura y dominio de valores (rangos, categorías, largos), incluidas
las restricciones que el schema enviado a la API no puede expresar.
"""

from pydantic import BaseModel, ValidationError

from src.esquemas import RespuestaTriage, RespuestaTriageConRazonamiento


def modelo_de_salida(con_razonamiento: bool) -> type[BaseModel]:
    return RespuestaTriageConRazonamiento if con_razonamiento else RespuestaTriage


def validar(datos: dict | None, texto_crudo: str, con_razonamiento: bool) -> tuple[RespuestaTriage | None, str | None]:
    """Devuelve (contrato, None) si es válido, o (None, descripción del error) si no.

    La descripción del error está pensada para devolvérsela al modelo en el reintento:
    tiene que decir qué campo falló y por qué, no sólo "inválido".
    """
    if datos is None:
        return None, (
            "la respuesta no era un objeto JSON completo (posiblemente se cortó por longitud). "
            f"Inicio de lo recibido: {texto_crudo[:80]!r}. Respondé de forma más breve."
        )
    try:
        validado = modelo_de_salida(con_razonamiento).model_validate(datos)
    except ValidationError as error:
        problemas = "; ".join(
            f"campo '{'.'.join(str(p) for p in e['loc']) or '(raíz)'}': {e['msg']}" for e in error.errors()
        )
        return None, problemas
    if isinstance(validado, RespuestaTriageConRazonamiento):
        return validado.a_contrato(), None
    return validado, None
