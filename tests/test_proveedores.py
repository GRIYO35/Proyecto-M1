"""Tests deterministas del fallback entre proveedores: no llaman a ninguna API."""

import json

import pytest

from src import metricas
from src.config import ConfigProveedor
from src.prompts import cargar_prompt
from src.proveedores.base import ProveedorLLM, ResultadoAPI
from src.proveedores.errores import ErrorAutenticacion, ErrorDefinitivo
from src.triage import procesar_consulta

VALIDO = json.dumps({"answer": "Hola, revisamos tu cobro.", "confidence": 0.9, "category": "billing", "actions": ["Verificar el cobro"]})


class ProveedorFalso(ProveedorLLM):
    def __init__(self, nombre: str, error: Exception | None = None):
        super().__init__(ConfigProveedor(f"modelo-{nombre}", 100, 5, 0.0, 0.0))
        self.nombre = nombre
        self.error = error
        self.llamadas = 0

    def _llamar_api(self, sistema, mensaje_usuario, schema):
        self.llamadas += 1
        if self.error:
            raise self.error
        return ResultadoAPI(VALIDO, 10, 10)


@pytest.fixture(autouse=True)
def logs_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(metricas, "RUTA_METRICAS", tmp_path / "metrics.jsonl")
    monkeypatch.setattr(metricas, "RUTA_SEGURIDAD", tmp_path / "seguridad.jsonl")
    return tmp_path


def test_api_key_rechazada_pasa_al_respaldo_sin_reintentar(logs_temporales):
    principal = ProveedorFalso("principal", ErrorAutenticacion("credencial rechazada (401)"))
    respaldo = ProveedorFalso("respaldo")
    resultado = procesar_consulta("me cobraron dos veces", [principal, respaldo], cargar_prompt("v5_seguro"))

    assert resultado.respuesta.category == "billing"
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)
    lineas = [json.loads(l) for l in (logs_temporales / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(l["proveedor"], l["exito"]) for l in lineas] == [("principal", False), ("respaldo", True)]


def test_error_definitivo_no_pasa_al_respaldo():
    principal = ProveedorFalso("principal", ErrorDefinitivo("request rechazada (400)"))
    respaldo = ProveedorFalso("respaldo")
    with pytest.raises(ErrorDefinitivo):
        procesar_consulta("me cobraron dos veces", [principal, respaldo], cargar_prompt("v5_seguro"))
    assert respaldo.llamadas == 0
