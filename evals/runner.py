"""Suite de evaluación: corre los datasets contra versiones de prompt y decide con datos.

Pipeline (Clase 5):
  1. Registro estructurado por caso: consulta, categoría esperada y obtenida, tokens,
     costo, latencia, respuesta y razonamiento, en un .jsonl por corrida (nunca se pisa).
  2. Exact match para la categoría (salida cerrada), global / por categoría / por dataset.
  3. LLM-as-judge sólo para el razonamiento de las versiones chain-of-thought (evals/juez.py).
  4. Umbrales fijados de antemano en config/evals.yaml: una versión con alguna categoría
     bajo el piso (o con falsos positivos de guardrails) queda descartada.
  5. Costo por acierto y un reporte en markdown que se lee sin correr nada.

Uso (desde la carpeta del proyecto):
    python -m evals.runner                                   # todas las versiones, ambos datasets
    python -m evals.runner --prompts v3_few_shot v5_seguro --repeticiones 3
    python -m evals.runner --sin-juez                        # no llama al juez (más barato)

Sale con código 4 si la versión en uso (PROMPT_VERSION) queda descartada: sirve para CI.
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# Ejecutado como script (`python evals/runner.py`), Python no ve los paquetes del
# proyecto: se agrega la raíz al path, igual que en src/main.py.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from evals.juez import cargar_config_evals, crear_juez, juzgar
from src.esquemas import CATEGORIAS
from src.guardrails import PERMITIR, mas_severa
from src.prompts import Prompt, cargar_prompt, versiones_disponibles
from src.proveedores import ProveedorLLM, crear_proveedor
from src.triage import procesar_consulta

CARPETA_EVALS = Path(__file__).resolve().parent
DATASETS_POR_DEFECTO = [CARPETA_EVALS / "dataset.jsonl", CARPETA_EVALS / "dataset_holdout.jsonl"]
CARPETA_RESULTADOS = CARPETA_EVALS / "resultados"
CARPETA_REPORTES = CARPETA_EVALS / "reportes"
RUTA_ULTIMO_REPORTE = CARPETA_EVALS / "reporte.md"
VERSION_EN_USO_POR_DEFECTO = "v5_seguro"


# ---------- 1. Registro ----------

@dataclass
class RegistroEval:
    dataset: str
    caso_id: str
    repeticion: int
    version_prompt: str
    modelo: str
    texto: str
    dificultad: str
    categoria_esperada: str
    categoria_obtenida: str | None
    correcto: bool
    confianza: float | None
    input_tokens: int
    output_tokens: int
    costo_usd: float
    latencia_s: float
    llamadas: int
    respuesta: str | None
    razonamiento: str | None
    guardrails: str | None
    motivos_guardrails: list[str]
    error: str | None
    timestamp: float
    # Sólo para versiones con razonamiento (LLM-as-judge).
    juez_veredicto: str | None = None
    juez_motivo: str | None = None
    juez_costo_usd: float = 0.0


def cargar_dataset(ruta: Path) -> list[dict]:
    casos = [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]
    return [caso | {"dataset": ruta.stem} for caso in casos]


def exact_match(categoria_modelo: str | None, categoria_referencia: str) -> bool:
    if categoria_modelo is None:
        return False
    return categoria_modelo.strip().lower() == categoria_referencia.strip().lower()


def evaluar_caso(caso: dict, repeticion: int, proveedores: list[ProveedorLLM], prompt: Prompt) -> RegistroEval:
    base = {
        "dataset": caso["dataset"],
        "caso_id": caso["id"],
        "repeticion": repeticion,
        "version_prompt": prompt.version,
        "modelo": proveedores[0].config.modelo,
        "texto": caso["consulta"],
        "dificultad": caso["dificultad"],
        "categoria_esperada": caso["categoria_esperada"],
        "timestamp": time.time(),
    }
    try:
        resultado = procesar_consulta(caso["consulta"], proveedores, prompt)
    except Exception as error:  # un caso que falla no corta la corrida: cuenta como desacierto
        return RegistroEval(
            **base, categoria_obtenida=None, correcto=False, confianza=None, input_tokens=0, output_tokens=0,
            costo_usd=0.0, latencia_s=0.0, llamadas=0, respuesta=None, razonamiento=None, guardrails=None,
            motivos_guardrails=[], error=f"{type(error).__name__}: {error}",
        )
    return RegistroEval(
        **base,
        categoria_obtenida=resultado.respuesta.category,
        correcto=exact_match(resultado.respuesta.category, caso["categoria_esperada"]),
        confianza=resultado.respuesta.confidence,
        input_tokens=sum(llamada.prompt_tokens for llamada in resultado.llamadas),
        output_tokens=sum(llamada.completion_tokens for llamada in resultado.llamadas),
        costo_usd=resultado.costo_usd,
        latencia_s=resultado.latencia_s,
        llamadas=len(resultado.llamadas),
        respuesta=resultado.respuesta.answer,
        razonamiento=resultado.razonamiento,
        # Todos los tickets del dataset son legítimos: algo distinto de "permitir" es un falso positivo.
        guardrails=mas_severa(resultado.accion_entrada, resultado.accion_salida),
        motivos_guardrails=resultado.motivos,
        error=None,
    )


def guardar_corrida(registros: list[RegistroEval], marca: str, proveedor: str, version: str) -> Path:
    ruta = CARPETA_RESULTADOS / f"{marca}_{proveedor}_{version}.jsonl"
    with ruta.open("w", encoding="utf-8") as archivo:
        for registro in registros:
            archivo.write(json.dumps(asdict(registro), ensure_ascii=False) + "\n")
    return ruta


# ---------- 2-4. Métricas, juez y umbrales ----------

def puntaje(aciertos: list[bool]) -> dict:
    return {"aciertos": sum(aciertos), "n": len(aciertos), "score": sum(aciertos) / len(aciertos) if aciertos else 0.0}


def puntaje_agrupado(registros: list[RegistroEval], clave, valor=lambda r: r.correcto) -> dict[str, dict]:
    grupos: dict[str, list[bool]] = defaultdict(list)
    for r in registros:
        grupos[clave(r)].append(valor(r))
    return {nombre: puntaje(aciertos) for nombre, aciertos in grupos.items()}


def chequear_regresion(por_categoria: dict[str, dict], umbral: float) -> list[str]:
    """Categorías cuyo score no llega al umbral mínimo fijado de antemano."""
    return [categoria for categoria, p in por_categoria.items() if p["score"] < umbral]


def costo_por_acierto(costo_total_usd: float, aciertos: int) -> float:
    """Cuánto costó, en promedio, cada caso que la versión clasificó bien."""
    return costo_total_usd / aciertos if aciertos else float("inf")


def consistencia(registros: list[RegistroEval]) -> float:
    """% de casos que recibieron siempre la misma categoría en todas las repeticiones."""
    por_caso = defaultdict(set)
    for r in registros:
        por_caso[r.caso_id].add(r.categoria_obtenida)
    return sum(len(categorias) == 1 for categorias in por_caso.values()) / len(por_caso)


def aplicar_juez(registros: list[RegistroEval], casos: dict[str, dict], hilos: int) -> None:
    """Juzga el razonamiento de la primera repetición de cada caso (las otras repiten el caso)."""
    juez, repeticiones = crear_juez("suite")
    a_juzgar = [r for r in registros if r.repeticion == 1 and r.error is None]

    def juzgar_registro(r: RegistroEval) -> None:
        juicio = juzgar(juez, r.texto, casos[r.caso_id]["criterio_razonamiento"], r.razonamiento, repeticiones)
        r.juez_veredicto, r.juez_motivo, r.juez_costo_usd = juicio.veredicto, juicio.motivos[0] if juicio.motivos else None, juicio.costo_usd

    with ThreadPoolExecutor(max_workers=hilos) as pool:
        list(pool.map(juzgar_registro, a_juzgar))


def resumir(version: str, registros: list[RegistroEval], umbrales: dict) -> dict:
    ok = [r for r in registros if r.error is None]
    latencias = sorted(r.latencia_s for r in ok) or [0.0]
    por_categoria = puntaje_agrupado(registros, lambda r: r.categoria_esperada)
    por_categoria = {c: por_categoria[c] for c in CATEGORIAS if c in por_categoria}
    costo_total = sum(r.costo_usd for r in ok)
    aciertos = sum(r.correcto for r in registros)
    falsos_positivos = sum(r.guardrails != PERMITIR for r in ok)

    resumen = {
        "version": version,
        "ejecuciones": len(registros),
        "aciertos": aciertos,
        "accuracy": aciertos / len(registros),
        "por_categoria": por_categoria,
        "por_dataset": puntaje_agrupado(registros, lambda r: r.dataset),
        "por_dificultad": puntaje_agrupado(registros, lambda r: r.dificultad),
        "consistencia": consistencia(registros),
        "costo_total_usd": costo_total,
        "costo_por_consulta_usd": costo_total / len(ok) if ok else 0.0,
        "costo_por_acierto_usd": costo_por_acierto(costo_total, aciertos),
        "latencia_media_s": statistics.mean(latencias),
        "latencia_p95_s": latencias[min(len(latencias) - 1, round(0.95 * (len(latencias) - 1)))],
        "tokens_entrada_promedio": statistics.mean(r.input_tokens for r in ok) if ok else 0,
        "tokens_salida_promedio": statistics.mean(r.output_tokens for r in ok) if ok else 0,
        "reintentos_contrato": sum(r.llamadas - 1 for r in ok),
        "falsos_positivos_guardrails": falsos_positivos,
        "errores": len(registros) - len(ok),
        "desaciertos": Counter(f"{r.caso_id}: {r.categoria_esperada} -> {r.categoria_obtenida}" for r in registros if not r.correcto),
        "juez": None,
    }

    juzgados = [r for r in registros if r.juez_veredicto in ("si", "no")]
    if juzgados:
        resumen["juez"] = {
            **puntaje([r.juez_veredicto == "si" for r in juzgados]),
            "por_categoria": puntaje_agrupado(juzgados, lambda r: r.categoria_esperada, lambda r: r.juez_veredicto == "si"),
            "costo_usd": sum(r.juez_costo_usd for r in registros),
            "errores": sum(r.juez_veredicto == "error" for r in registros),
            "discrepancias": [
                {"caso": r.caso_id, "exact_match": r.correcto, "juez": r.juez_veredicto, "motivo": r.juez_motivo}
                for r in juzgados
                if r.correcto != (r.juez_veredicto == "si")
            ],
        }

    motivos_descarte = []
    bajo_umbral = chequear_regresion(por_categoria, umbrales["umbral_minimo_por_categoria"])
    if bajo_umbral:
        motivos_descarte.append(
            "categorías bajo el umbral: " + ", ".join(f"{c} {por_categoria[c]['score']:.1%}" for c in bajo_umbral)
        )
    if falsos_positivos > umbrales["maximo_falsos_positivos_guardrails"]:
        motivos_descarte.append(f"{falsos_positivos} falsos positivos de guardrails")
    if resumen["juez"] and resumen["juez"]["score"] < umbrales["umbral_minimo_juez"]:
        motivos_descarte.append(f"razonamiento bajo el umbral del juez ({resumen['juez']['score']:.1%})")
    resumen["bajo_umbral"] = bajo_umbral
    resumen["motivos_descarte"] = motivos_descarte
    resumen["candidata"] = not motivos_descarte
    return resumen


# ---------- Salida por terminal ----------

def fmt(p: dict) -> str:
    return f"{p['aciertos']}/{p['n']} ({p['score']:.0%})"


def imprimir_resumen(r: dict) -> None:
    estado = "candidata" if r["candidata"] else "DESCARTADA (" + "; ".join(r["motivos_descarte"]) + ")"
    print(f"\n=== {r['version']} === {estado}")
    print(f"Accuracy: {r['aciertos']}/{r['ejecuciones']} ({r['accuracy']:.1%}) | Consistencia: {r['consistencia']:.0%}")
    print("Por categoría: " + " | ".join(f"{c}: {fmt(p)}" for c, p in r["por_categoria"].items()))
    print("Por dataset:   " + " | ".join(f"{d}: {fmt(p)}" for d, p in r["por_dataset"].items()))
    print(
        f"Costo USD {r['costo_total_usd']:.5f} ({r['costo_por_consulta_usd']:.6f}/consulta, "
        f"{r['costo_por_acierto_usd']:.6f}/acierto) | Latencia media {r['latencia_media_s']:.2f}s, "
        f"p95 {r['latencia_p95_s']:.2f}s | Falsos positivos guardrails: {r['falsos_positivos_guardrails']} | "
        f"Errores: {r['errores']}"
    )
    if r["juez"]:
        print(f"Juez (razonamiento): {fmt(r['juez'])} | discrepancias con exact match: {len(r['juez']['discrepancias'])}")
    for desacierto, veces in r["desaciertos"].items():
        print(f"  ✗ {desacierto} (x{veces})")


# ---------- 5. Reporte ----------

def tabla(encabezado: list[str], filas: list[list[str]], alineacion: list[str] | None = None) -> list[str]:
    alineacion = alineacion or ["---"] * len(encabezado)
    return ["| " + " | ".join(encabezado) + " |", "|" + "|".join(alineacion) + "|"] + ["| " + " | ".join(f) + " |" for f in filas]


def usd(valor: float) -> str:
    return f"${valor:.6f}" if valor != float("inf") else "∞"


def generar_reporte(resumenes: list[dict], meta: dict, umbrales: dict) -> str:
    categorias = list(CATEGORIAS)
    datasets = meta["datasets"]
    lineas = [
        "# Reporte de evaluación · Triage Nubbix",
        "",
        f"Generado automáticamente por `python -m evals.runner` el {meta['fecha']}. "
        "No editar a mano: la decisión y su justificación están en `docs/evaluacion.md`.",
        "",
        "## 1. Qué se evaluó",
        "",
        f"- **Modelo evaluado:** {meta['modelo']} ({meta['proveedor']}). **Juez:** {meta['juez'] or 'no se usó'}.",
        f"- **Versiones de prompt:** {', '.join(r['version'] for r in resumenes)}.",
        "- **Datasets** (referencia fijada antes de correr el modelo):",
    ]
    for nombre, info in datasets.items():
        lineas.append(f"  - `{nombre}.jsonl`: {info['casos']} casos ({info['detalle']}).")
    lineas += [
        f"- **Repeticiones:** {meta['repeticiones']} → {resumenes[0]['ejecuciones']} ejecuciones por versión.",
        f"- **Umbrales** (`config/evals.yaml`, fijados antes): {umbrales['umbral_minimo_por_categoria']:.0%} por categoría · "
        f"{umbrales['umbral_minimo_juez']:.0%} de razonamientos aprobados por el juez · "
        f"{umbrales['maximo_falsos_positivos_guardrails']} falsos positivos de guardrails.",
        "",
        "## 2. Resultado por versión",
        "",
    ]
    filas = []
    for r in resumenes:
        filas.append(
            [
                f"**{r['version']}**",
                "candidata" if r["candidata"] else "**descartada**",
                f"{r['accuracy']:.1%}",
                *[f"{r['por_categoria'][c]['score']:.0%}" if c in r["por_categoria"] else "-" for c in categorias],
                ", ".join(r["bajo_umbral"]) or "ninguna",
                f"{r['juez']['score']:.0%}" if r["juez"] else "n/a",
                str(r["falsos_positivos_guardrails"]),
                f"{r['consistencia']:.0%}",
                usd(r["costo_por_consulta_usd"]),
                usd(r["costo_por_acierto_usd"]),
                f"{r['latencia_p95_s']:.2f}",
            ]
        )
    lineas += tabla(
        ["Versión", "Estado", "Exact match", *categorias, "Bajo umbral", "Juez", "FP guardrails", "Consistencia",
         "Costo/consulta", "Costo por acierto", "Latencia p95 (s)"],
        filas,
        ["---", "---", "---:", *["---:"] * len(categorias), "---", "---:", "---:", "---:", "---:", "---:", "---:"],
    )

    lineas += ["", "### Exact match por dataset y dificultad", ""]
    columnas = list(datasets) + ["facil", "media", "dificil"]
    lineas += tabla(
        ["Versión", *columnas],
        [[r["version"], *[fmt((r["por_dataset"] | r["por_dificultad"])[c]) if c in (r["por_dataset"] | r["por_dificultad"]) else "-" for c in columnas]] for r in resumenes],
    )
    lineas += [
        "",
        "El dataset held-out no se usó para diseñar ningún prompt: si una versión baja mucho ahí "
        "respecto del principal, su mejora no generaliza.",
    ]

    con_juez = [r for r in resumenes if r["juez"]]
    if con_juez:
        lineas += ["", "## 3. LLM-as-judge (razonamiento de las versiones chain-of-thought)", ""]
        lineas += [
            "Sólo aplica a las versiones que escriben un razonamiento; para las demás, exact match "
            "ya es la métrica completa. Se juzga la 1.ª repetición de cada caso contra su "
            "`criterio_razonamiento`. El juez se validó antes contra casos con veredicto humano "
            "(`python -m evals.juez`).",
            "",
        ]
        for r in con_juez:
            j = r["juez"]
            lineas.append(
                f"**{r['version']}**: {fmt(j)} razonamientos aprueban el criterio · "
                + " · ".join(f"{c} {fmt(p)}" for c, p in j["por_categoria"].items())
                + f" · costo del juez USD {j['costo_usd']:.4f}"
            )
            if j["discrepancias"]:
                lineas += ["", "Casos donde exact match y juez no coinciden:", ""]
                lineas += tabla(
                    ["Caso", "Exact match", "Juez", "Motivo del juez"],
                    [[d["caso"], "acierto" if d["exact_match"] else "error", d["juez"], (d["motivo"] or "").replace("|", "/")] for d in j["discrepancias"]],
                )
            else:
                lineas.append("\nNo hay discrepancias entre exact match y juez.")

    lineas += ["", "## 4. Desaciertos", ""]
    hay = False
    for r in resumenes:
        for desacierto, veces in r["desaciertos"].items():
            lineas.append(f"- {r['version']} · {desacierto} ({veces} de {meta['repeticiones']})")
            hay = True
    if not hay:
        lineas.append("Ninguna versión tuvo desaciertos.")

    # Las tres preguntas que un reporte tiene que contestar.
    candidatas = [r for r in resumenes if r["candidata"]]
    descartadas = [r for r in resumenes if not r["candidata"]]
    lineas += ["", "## 5. Respuestas", "", "**¿Alguna versión queda descartada por el umbral?** "]
    lineas[-1] += (
        "; ".join(f"{r['version']}: {'; '.join(r['motivos_descarte'])}" for r in descartadas) + "."
        if descartadas else "No: todas las versiones superan los umbrales."
    )
    if candidatas:
        mejor_score = max(r["accuracy"] for r in candidatas)
        mejores = [r for r in candidatas if r["accuracy"] == mejor_score]
        mas_barata = min(candidatas, key=lambda r: r["costo_por_acierto_usd"])
        mejor_de_las_mejores = min(mejores, key=lambda r: r["costo_por_acierto_usd"])
        lineas += [
            "",
            f"**¿La de mejor score es también la de mejor costo por acierto?** Mejor score entre candidatas: "
            f"{', '.join(r['version'] for r in mejores)} ({mejor_score:.1%}). Mejor costo por acierto: "
            f"{mas_barata['version']} ({usd(mas_barata['costo_por_acierto_usd'])}, {mas_barata['accuracy']:.1%}).",
        ]
        if mas_barata in mejores:
            lineas[-1] += f" Coinciden: {mas_barata['version']} es la mejor en las dos métricas."
        else:
            extra = mejor_de_las_mejores["costo_por_acierto_usd"] / mas_barata["costo_por_acierto_usd"] - 1
            lineas[-1] += (
                f" No coinciden: {mejor_de_las_mejores['version']} cuesta {extra:+.0%} por acierto para ganar "
                f"{(mejor_score - mas_barata['accuracy']) * 100:.1f} puntos de accuracy."
            )
    return "\n".join(lineas) + "\n"


# ---------- main ----------

def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    load_dotenv()

    parser = argparse.ArgumentParser(description="Suite de evaluación de versiones de prompt.")
    parser.add_argument("--prompts", nargs="+", default=versiones_disponibles())
    parser.add_argument("--proveedor", default=os.getenv("LLM_PROVEEDOR", "anthropic"))
    parser.add_argument("--datasets", nargs="+", type=Path, default=DATASETS_POR_DEFECTO)
    parser.add_argument("--hilos", type=int, default=4, help="Consultas en paralelo (cuidado con el rate limit).")
    parser.add_argument("--repeticiones", type=int, default=1, help="Veces que se corre cada caso (mide consistencia).")
    parser.add_argument("--sin-juez", action="store_true", help="No juzga los razonamientos (ahorra el costo del juez).")
    parser.add_argument("--version-en-uso", default=os.getenv("PROMPT_VERSION", VERSION_EN_USO_POR_DEFECTO))
    argumentos = parser.parse_args()

    umbrales = cargar_config_evals()
    casos = [caso for ruta in argumentos.datasets for caso in cargar_dataset(ruta)]
    casos_por_id = {caso["id"]: caso for caso in casos}
    proveedores = [crear_proveedor(argumentos.proveedor)]
    CARPETA_RESULTADOS.mkdir(exist_ok=True)
    CARPETA_REPORTES.mkdir(exist_ok=True)
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(
        f"Casos: {len(casos)} x {argumentos.repeticiones} repeticiones | Proveedor: {argumentos.proveedor} | "
        f"Prompts: {', '.join(argumentos.prompts)}"
    )
    trabajos = [(rep, caso) for rep in range(1, argumentos.repeticiones + 1) for caso in casos]

    resumenes = []
    juez_usado = None
    for version in argumentos.prompts:
        prompt = cargar_prompt(version)
        with ThreadPoolExecutor(max_workers=argumentos.hilos) as pool:
            registros = list(pool.map(lambda t: evaluar_caso(t[1], t[0], proveedores, prompt), trabajos))
        if prompt.razonamiento and not argumentos.sin_juez:
            aplicar_juez(registros, casos_por_id, argumentos.hilos)
            juez_usado = crear_juez("suite")[0].config.modelo
        resumen = resumir(version, registros, umbrales)
        imprimir_resumen(resumen)
        resumenes.append(resumen)
        guardar_corrida(registros, marca, argumentos.proveedor, version)

    datasets = {}
    for ruta in argumentos.datasets:
        del_dataset = [c for c in casos if c["dataset"] == ruta.stem]
        por_categoria = Counter(c["categoria_esperada"] for c in del_dataset)
        dificiles = sum(c["dificultad"] == "dificil" for c in del_dataset)
        datasets[ruta.stem] = {
            "casos": len(del_dataset),
            "detalle": " · ".join(f"{por_categoria[c]} {c}" for c in CATEGORIAS) + f" · {dificiles} difíciles",
        }
    meta = {
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "proveedor": argumentos.proveedor,
        "modelo": proveedores[0].config.modelo,
        "juez": juez_usado,
        "repeticiones": argumentos.repeticiones,
        "datasets": datasets,
    }
    reporte = generar_reporte(resumenes, meta, umbrales)
    (CARPETA_REPORTES / f"{marca}_reporte.md").write_text(reporte, encoding="utf-8")
    RUTA_ULTIMO_REPORTE.write_text(reporte, encoding="utf-8")
    (CARPETA_RESULTADOS / f"{marca}_resumen.json").write_text(
        json.dumps({"meta": meta, "umbrales": umbrales, "resumenes": resumenes}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nRegistros en {CARPETA_RESULTADOS} · Reporte en {RUTA_ULTIMO_REPORTE}")

    en_uso = next((r for r in resumenes if r["version"] == argumentos.version_en_uso), None)
    if en_uso and not en_uso["candidata"]:
        print(f"⚠ La versión en uso ({en_uso['version']}) no pasa los umbrales: {'; '.join(en_uso['motivos_descarte'])}")
        sys.exit(4)


if __name__ == "__main__":
    main()
