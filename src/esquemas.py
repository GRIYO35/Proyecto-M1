"""Contrato de salida del servicio.

Los nombres de los campos y los valores de `category` los fija la consigna,
por eso quedan en inglés aunque el resto del código esté en castellano.

`CATEGORIAS` es la única fuente de verdad: de acá salen el tipo `Categoria`,
el JSON Schema que se le exige al modelo y la lista que se inyecta en el prompt.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CATEGORIAS: dict[str, str] = {
    "billing": "cobros, facturas emitidas por Nubbix, pagos de la suscripción, medios de pago, reintegros, precio de lo ya contratado.",
    "technical": "errores, fallas, lentitud, integraciones caídas, algo del sistema que no funciona como debería.",
    "account": "usuarios, permisos, acceso y contraseñas, datos del titular o administrador, alta, baja o fusión de la cuenta.",
    "other": "todo lo demás: consultas comerciales o de preventa, planes nuevos, sugerencias, capacitaciones, quejas sobre la atención, mensajes sin contenido.",
}

Categoria = Literal.__getitem__(tuple(CATEGORIAS))

ConfianzaField = Field(ge=0, le=1, description="Confianza en la categoría elegida, de 0 a 1.")


class RespuestaTriage(BaseModel):
    """Contrato público: lo que devuelve la CLI."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=1200, description="Respuesta breve y cordial para el cliente, en español.")
    confidence: float = ConfianzaField
    category: Categoria = Field(description="Categoría de la consulta.")
    actions: list[str] = Field(
        min_length=1, max_length=6, description="Acciones concretas sugeridas para el agente humano de soporte."
    )


class RespuestaTriageConRazonamiento(BaseModel):
    """Variante interna para prompts chain-of-thought.

    `razonamiento` va primero a propósito: el modelo genera los campos en orden,
    así la categoría queda condicionada por el razonamiento ya escrito. Se descarta
    antes de devolver el contrato público.
    """

    model_config = ConfigDict(extra="forbid")

    razonamiento: str = Field(
        min_length=1, description="2 a 4 oraciones: qué menciona el cliente, qué pide concretamente y qué categoría gana."
    )
    answer: str = RespuestaTriage.model_fields["answer"]
    confidence: float = ConfianzaField
    category: Categoria = Field(description="Categoría de la consulta.")
    actions: list[str] = RespuestaTriage.model_fields["actions"]

    def a_contrato(self) -> RespuestaTriage:
        return RespuestaTriage.model_validate(self.model_dump(exclude={"razonamiento"}))


# Restricciones que los proveedores no aceptan dentro del schema de salida estructurada.
# Se quitan del schema que viaja a la API, pero Pydantic las sigue validando del lado nuestro.
_RESTRICCIONES_NO_SOPORTADAS = {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "title"}


def _limpiar(nodo):
    if isinstance(nodo, dict):
        return {k: _limpiar(v) for k, v in nodo.items() if k not in _RESTRICCIONES_NO_SOPORTADAS}
    if isinstance(nodo, list):
        return [_limpiar(v) for v in nodo]
    return nodo


def schema_para_api(modelo: type[BaseModel]) -> dict:
    """JSON Schema derivado del modelo de Pydantic, sin las restricciones que la API rechaza."""
    return _limpiar(modelo.model_json_schema())
