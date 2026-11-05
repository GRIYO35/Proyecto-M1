# Reporte de evaluación · Triage Nubbix

Generado automáticamente por `python -m evals.runner` el 2026-11-04 21:18. No editar a mano: la decisión y su justificación están en `docs/evaluacion.md`.

## 1. Qué se evaluó

- **Modelo evaluado:** claude-haiku-5-5 (anthropic). **Juez:** claude-sonnet-5-5.
- **Versiones de prompt:** v1_zero_shot, v2_estructurado, v3_few_shot, v4_cot, v5_seguro.
- **Datasets** (referencia fijada antes de correr el modelo):
  - `dataset.jsonl`: 48 casos (12 billing · 12 technical · 12 account · 12 other · 23 difíciles).
  - `dataset_holdout.jsonl`: 16 casos (4 billing · 4 technical · 4 account · 4 other · 6 difíciles).
- **Repeticiones:** 3 → 192 ejecuciones por versión.
- **Umbrales** (`config/evals.yaml`, fijados antes): 85% por categoría · 80% de razonamientos aprobados por el juez · 0 falsos positivos de guardrails.

## 2. Resultado por versión

| Versión | Estado | Exact match | billing | technical | account | other | Bajo umbral | Juez | FP guardrails | Consistencia | Costo/consulta | Costo por acierto | Latencia p95 (s) |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
| **v1_zero_shot** | candidata | 96.4% | 92% | 94% | 100% | 100% | ninguna | n/a | 0 | 98% | $0.000238 | $0.000246 | 3.03 |
| **v2_estructurado** | candidata | 98.4% | 98% | 96% | 100% | 100% | ninguna | n/a | 0 | 97% | $0.000256 | $0.000260 | 2.83 |
| **v3_few_shot** | candidata | 100.0% | 100% | 100% | 100% | 100% | ninguna | n/a | 0 | 100% | $0.000284 | $0.000284 | 2.83 |
| **v4_cot** | candidata | 99.0% | 96% | 100% | 100% | 100% | ninguna | 95% | 0 | 98% | $0.000356 | $0.000360 | 3.18 |
| **v5_seguro** | candidata | 100.0% | 100% | 100% | 100% | 100% | ninguna | n/a | 0 | 100% | $0.000304 | $0.000304 | 2.62 |

### Exact match por dataset y dificultad

| Versión | dataset | dataset_holdout | facil | media | dificil |
|---|---|---|---|---|---|
| v1_zero_shot | 137/144 (95%) | 48/48 (100%) | 53/54 (98%) | 51/51 (100%) | 81/87 (93%) |
| v2_estructurado | 141/144 (98%) | 48/48 (100%) | 54/54 (100%) | 51/51 (100%) | 84/87 (97%) |
| v3_few_shot | 144/144 (100%) | 48/48 (100%) | 54/54 (100%) | 51/51 (100%) | 87/87 (100%) |
| v4_cot | 142/144 (99%) | 48/48 (100%) | 54/54 (100%) | 49/51 (96%) | 87/87 (100%) |
| v5_seguro | 144/144 (100%) | 48/48 (100%) | 54/54 (100%) | 51/51 (100%) | 87/87 (100%) |

El dataset held-out no se usó para diseñar ningún prompt: si una versión baja mucho ahí respecto del principal, su mejora no generaliza.

## 3. LLM-as-judge (razonamiento de las versiones chain-of-thought)

Sólo aplica a las versiones que escriben un razonamiento; para las demás, exact match ya es la métrica completa. Se juzga la 1.ª repetición de cada caso contra su `criterio_razonamiento`. El juez se validó antes contra casos con veredicto humano (`python -m evals.juez`).

**v4_cot**: 61/64 (95%) razonamientos aprueban el criterio · billing 15/16 (94%) · technical 15/16 (94%) · account 15/16 (94%) · other 16/16 (100%) · costo del juez USD 0.1841

Casos donde exact match y juez no coinciden:

| Caso | Exact match | Juez | Motivo del juez |
|---|---|---|---|
| tec-05 | acierto | no | El razonamiento identifica correctamente que el problema es el cierre inesperado de la app (falla técnica) y descarta billing, pero no reconoce que 'factura' se refiere a las facturas que el cliente emite, que es la mitad del criterio. |
| acc-08 | acierto | no | El razonamiento identifica la baja de la cuenta y el borrado de datos, pero no nota que no hay un pedido de reintegro, que el criterio exige explícitamente. |

## 4. Desaciertos

- v1_zero_shot · bil-01: billing -> None (1 de 3)
- v1_zero_shot · bil-10: billing -> other (3 de 3)
- v1_zero_shot · tec-10: technical -> billing (3 de 3)
- v2_estructurado · tec-10: technical -> billing (2 de 3)
- v2_estructurado · bil-10: billing -> other (1 de 3)
- v4_cot · bil-04: billing -> account (2 de 3)

## 5. Respuestas

**¿Alguna versión queda descartada por el umbral?** No: todas las versiones superan los umbrales.

**¿La de mejor score es también la de mejor costo por acierto?** Mejor score entre candidatas: v3_few_shot, v5_seguro (100.0%). Mejor costo por acierto: v1_zero_shot ($0.000246, 96.4%). No coinciden: v3_few_shot cuesta +16% por acierto para ganar 3.6 puntos de accuracy.
