"""Tests deterministas de la suite de evaluación: no llaman a ninguna API."""

import json
from pathlib import Path

import pytest

from evals import runner
from evals.juez import juzgar
from src.config import ConfigProveedor
from src.esquemas import CATEGORIAS
from src.proveedores.base import ProveedorLLM, ResultadoAPI

CARPETA_EVALS = Path(__file__).resolve().parent.parent / "evals"
UMBRALES = {"umbral_minimo_por_categoria": 0.85, "umbral_minimo_juez": 0.80, "maximo_falsos_positivos_guardrails": 0}


# ---------- Datasets ----------

@pytest.mark.parametrize("archivo", ["dataset.jsonl", "dataset_holdout.jsonl"])
def test_datasets_bien_formados(archivo):
    casos = runner.cargar_dataset(CARPETA_EVALS / archivo)
    assert len({c["id"] for c in casos}) == len(casos), "ids repetidos"
    for c in casos:
        assert c["categoria_esperada"] in CATEGORIAS
        assert c["dificultad"] in ("facil", "media", "dificil")
        assert c["criterio_razonamiento"].strip(), f"{c['id']} sin criterio para el juez"


def test_categorias_balanceadas():
    casos = runner.cargar_dataset(CARPETA_EVALS / "dataset.jsonl") + runner.cargar_dataset(CARPETA_EVALS / "dataset_holdout.jsonl")
    por_categoria = {c: sum(caso["categoria_esperada"] == c for caso in casos) for c in CATEGORIAS}
    assert len(set(por_categoria.values())) == 1, por_categoria


def test_holdout_no_repite_casos_del_principal():
    principal = {c["consulta"] for c in runner.cargar_dataset(CARPETA_EVALS / "dataset.jsonl")}
    holdout = {c["consulta"] for c in runner.cargar_dataset(CARPETA_EVALS / "dataset_holdout.jsonl")}
    assert not principal & holdout


# ---------- Métricas ----------

@pytest.mark.parametrize(
    "obtenida, esperada, acierto",
    [("billing", "billing", True), (" Billing ", "billing", True), ("other", "billing", False), (None, "billing", False)],
)
def test_exact_match(obtenida, esperada, acierto):
    assert runner.exact_match(obtenida, esperada) is acierto


def test_chequear_regresion_detecta_la_categoria_floja_aunque_el_promedio_sea_alto():
    por_categoria = {
        "billing": runner.puntaje([True] * 10),
        "technical": runner.puntaje([True] * 10),
        "account": runner.puntaje([True] * 10),
        "other": runner.puntaje([True] * 8 + [False] * 2),  # 80%: bajo el 85%
    }
    assert runner.chequear_regresion(por_categoria, 0.85) == ["other"]


def test_costo_por_acierto():
    assert runner.costo_por_acierto(0.03, 10) == pytest.approx(0.003)
    assert runner.costo_por_acierto(0.03, 0) == float("inf")


def _registro(caso_id, esperada, obtenida, costo=0.001, guardrails="permitir", juez=None, rep=1):
    return runner.RegistroEval(
        dataset="dataset", caso_id=caso_id, repeticion=rep, version_prompt="vX", modelo="m", texto="t",
        dificultad="facil", categoria_esperada=esperada, categoria_obtenida=obtenida,
        correcto=runner.exact_match(obtenida, esperada), confianza=0.9, input_tokens=10, output_tokens=10,
        costo_usd=costo, latencia_s=1.0, llamadas=1, respuesta="r", razonamiento=None, guardrails=guardrails,
        motivos_guardrails=[], error=None, timestamp=0.0, juez_veredicto=juez,
    )


def _registros_perfectos(**kwargs):
    return [_registro(f"{c}-{i}", c, c, **kwargs) for c in CATEGORIAS for i in range(4)]


def test_resumen_descarta_por_categoria_bajo_umbral():
    registros = _registros_perfectos()
    registros[0] = _registro("billing-0", "billing", "technical")  # billing 3/4 = 75%
    resumen = runner.resumir("vX", registros, UMBRALES)
    assert resumen["bajo_umbral"] == ["billing"]
    assert not resumen["candidata"]


def test_resumen_descarta_por_falsos_positivos_de_guardrails():
    registros = _registros_perfectos()
    registros[0] = _registro("billing-0", "billing", "billing", guardrails="marcar")
    resumen = runner.resumir("vX", registros, UMBRALES)
    assert resumen["accuracy"] == 1.0
    assert not resumen["candidata"]


def test_resumen_juez_y_discrepancias():
    registros = _registros_perfectos(juez="si")
    registros[0].juez_veredicto = "no"  # acierta la categoría, pero el razonamiento no aplica el criterio
    resumen = runner.resumir("vX", registros, UMBRALES)
    assert resumen["juez"]["aciertos"] == len(registros) - 1
    assert [d["caso"] for d in resumen["juez"]["discrepancias"]] == ["billing-0"]


def test_reporte_contesta_las_tres_preguntas():
    cara = runner.resumir("v_cara", _registros_perfectos(costo=0.002), UMBRALES)
    barata_registros = _registros_perfectos(costo=0.001)
    barata_registros[0] = _registro("billing-0", "billing", "other", costo=0.001)
    barata_registros += [_registro(f"billing-x{i}", "billing", "billing", costo=0.001) for i in range(8)]  # billing 11/12
    barata = runner.resumir("v_barata", barata_registros, UMBRALES)
    meta = {"fecha": "hoy", "proveedor": "p", "modelo": "m", "juez": None, "repeticiones": 1,
            "datasets": {"dataset": {"casos": 16, "detalle": "x"}}}
    reporte = runner.generar_reporte([cara, barata], meta, UMBRALES)
    assert "## 1. Qué se evaluó" in reporte
    assert "¿Alguna versión queda descartada" in reporte
    assert "No coinciden: v_cara cuesta" in reporte


# ---------- Juez ----------

class JuezFalso(ProveedorLLM):
    nombre = "falso"

    def __init__(self, veredictos: list[str]):
        super().__init__(ConfigProveedor("juez-falso", 100, 5, 0.0, 0.0))
        self.veredictos = list(veredictos)

    def _llamar_api(self, sistema, mensaje_usuario, schema):
        return ResultadoAPI(json.dumps({"motivo": "m", "veredicto": self.veredictos.pop(0)}), 10, 10)


def test_juez_decide_por_mayoria():
    assert juzgar(JuezFalso(["si", "no", "si"]), "c", "cr", "razonamiento", 3).veredicto == "si"
    assert juzgar(JuezFalso(["no", "no", "si"]), "c", "cr", "razonamiento", 3).veredicto == "no"


def test_juez_empate_es_no():
    juicio = juzgar(JuezFalso(["si", "no"]), "c", "cr", "razonamiento", 2)
    assert juicio.veredicto == "no"


def test_juez_sin_razonamiento_no_llama_al_modelo():
    juez = JuezFalso([])
    assert juzgar(juez, "c", "cr", None, 3).veredicto == "no"


def test_juez_con_respuesta_invalida_no_corta_la_suite():
    juez = JuezFalso(["quizas"])  # fuera del Literal["si", "no"]
    assert juzgar(juez, "c", "cr", "razonamiento", 1).veredicto == "error"


# ---------- Salida de la CLI ----------

def test_salida_por_pipe_es_ascii_y_no_se_corrompe_con_otra_codificacion():
    from src.main import formatear_salida

    datos = {"answer": "¡Hola! Información de antigüedad"}
    por_pipe = formatear_salida(datos, es_terminal=False)
    assert por_pipe.isascii()
    # Un consumidor que lee en cp1252 (Windows) recupera exactamente el mismo texto.
    assert json.loads(por_pipe.encode("utf-8").decode("cp1252")) == datos
    assert "Información" in formatear_salida(datos, es_terminal=True)
