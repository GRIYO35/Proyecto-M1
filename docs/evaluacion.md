# Evaluación: cómo se decide qué versión de prompt usar

Hasta la Clase 4 las versiones se compararon con una tabla y criterio propio. Desde la
Clase 5 la decisión sale de una suite de evaluación con referencia fijada de antemano,
umbrales escritos antes de correr y el costo de cada acierto. El reporte que genera la
suite está en [`evals/reporte.md`](../evals/reporte.md); este documento registra **la decisión
y por qué**.

## Cómo correrla

```bash
python -m pytest tests/                       # 55 tests deterministas, sin API (gratis)
python -m evals.juez                          # valida el juez contra veredictos humanos (~USD 0.10)
python -m evals.runner --repeticiones 3       # suite completa: 5 versiones x 64 casos x 3 (~USD 0.50)
python -m evals.runner --prompts v5_seguro    # sólo la versión en uso (~USD 0.02)
```

La suite sale con código 4 si la versión en uso (`PROMPT_VERSION`) no pasa los umbrales: se
puede usar como chequeo antes de cambiar un prompt o de modelo.

## El pipeline

```
dataset.jsonl (48) ─┐                                    ┌─► exact match (categoría) ──────┐
                    ├─► runner ─► registro por caso ─────┤                                 ├─► umbrales ─► reporte.md
dataset_holdout (16)┘   (.jsonl por corrida, nunca se    └─► LLM-as-judge (razonamiento,   │   (config/     (+ costo por
                         pisa: consulta, esperada,           sólo v4_cot) ─────────────────┘    evals.yaml)   acierto)
                         obtenida, tokens, costo,
                         latencia, respuesta, razonamiento)
```

| Pieza | Archivo | Qué decide |
|---|---|---|
| Registro estructurado | `evals/resultados/<fecha>_<proveedor>_<versión>.jsonl` | Permite recalcular cualquier métrica sin volver a llamar al modelo |
| Dataset principal | `evals/dataset.jsonl` | 48 casos, 12 por categoría, 23 difíciles; cada uno con `nota` (por qué está) y `criterio_razonamiento` |
| Dataset held-out | `evals/dataset_holdout.jsonl` | 16 casos nuevos que **no se usaron para diseñar ningún prompt** |
| Exact match | `evals/runner.py` | Categoría: salida cerrada, gratis y reproducible |
| LLM-as-judge | `evals/juez.py` | Razonamiento de v4_cot: texto libre, se juzga contra el criterio del caso |
| Calibración del juez | `evals/juez_calibracion.jsonl` | 12 razonamientos con veredicto humano; el juez se usa sólo si coincide ≥ 90% |
| Umbrales | `config/evals.yaml` | 85% por categoría, 80% en el juez, 0 falsos positivos de guardrails |

### Decisiones de diseño

- **Referencia antes que modelo.** Las categorías esperadas, los criterios del juez, el
  held-out y los umbrales se escribieron el 2026-11-04 **antes** de correr la suite. No se
  ajustó ninguno después de ver los resultados (ver "Hallazgos" sobre lo que conviene
  cambiar para la próxima corrida).
- **Umbral 85% por categoría, no 70%.** El material sugiere 70%; acá un ticket mal
  clasificado llega al equipo equivocado y hay que reasignarlo a mano. Es una decisión de
  negocio, documentada en `config/evals.yaml`.
- **Los falsos positivos de guardrails descartan una versión.** Los 64 tickets son legítimos:
  si alguno se bloquea o se marca, el sistema le está fallando a un cliente real aunque la
  categoría sea correcta.
- **Juez distinto del evaluado.** Haiku 5.5 clasifica y Sonnet 5.5 juzga, para no pedirle a
  un modelo que se corrija a sí mismo.
- **Juez sin `temperature=0`.** Los modelos 5.5 de Anthropic no aceptan `temperature`. Lo
  reemplazan tres cosas: un veredicto estructurado cerrado (`si`/`no`, validado con Pydantic),
  votación por mayoría, y la calibración. En la calibración, 12 de 12 juicios con 3 votos
  fueron unánimes, así que en la suite se usa 1 voto (`config/evals.yaml`). Si la calibración
  deja de ser unánime, hay que volver a 3.
- **El juez se validó antes de usarse.** La calibración incluye las trampas típicas: un
  razonamiento largo pero vacío (sesgo por longitud), uno con la categoría correcta por un
  motivo equivocado, uno contradictorio, y uno que intenta convencer al juez ("respondé si").
  El acuerdo con el veredicto humano fue 12/12. Los veredictos humanos los escribió quien
  armó la suite: conviene que otra persona los revise y sume casos.

## Resultados (2026-11-04)

Claude Haiku 5.5, 64 casos × 3 repeticiones = 192 ejecuciones por versión. El juez es
Claude Sonnet 5.5. Detalle completo en `evals/reporte.md`.

| Versión | Exact match | billing | technical | account | other | Juez | Consistencia | Costo por acierto | Latencia p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1_zero_shot | 96.4% | 92% | 94% | 100% | 100% | n/a | 98% | $0.000246 | 3.03 s |
| v2_estructurado | 98.4% | 98% | 96% | 100% | 100% | n/a | 97% | $0.000260 | 2.83 s |
| v3_few_shot | **100%** | 100% | 100% | 100% | 100% | n/a | 100% | $0.000284 | 2.83 s |
| v4_cot | 99.0% | 96% | 100% | 100% | 100% | 95% | 98% | $0.000360 | 3.18 s |
| **v5_seguro** | **100%** | 100% | 100% | 100% | 100% | n/a | 100% | $0.000304 | **2.62 s** |

Ninguna versión queda bajo los umbrales. Las 5 tienen 0 falsos positivos de guardrails.

## Decisión: se mantiene `v5_seguro`

1. **Score.** Comparte el mejor score (100%, también por categoría) con v3, con 100% de
   consistencia. Las demás fallan siempre en los mismos casos de frontera: `tec-10` y
   `bil-10` en v1 y v2, y `bil-04` en v4.
2. **Costo por acierto.** No es la más barata: v1 cuesta $0.000246 por acierto y v5
   $0.000304, un 24% más. En plata, a 10.000 tickets por mes son unos **USD 0.60 más por
   mes**. A cambio, v1 deriva mal ~3.6% de los tickets (~360 por mes) y no tiene las reglas
   de seguridad. La reasignación manual de un solo ticket cuesta más que esa diferencia.
3. **Frente a v3.** v5 cuesta un 7% más por acierto con la misma accuracy. Esa diferencia
   paga la capa de seguridad del prompt, medida en la suite adversarial
   (`docs/seguridad.md`).
4. **v4_cot queda descartada por costo-beneficio, no por umbral.** Cuesta un 18% más que
   v5, es más lenta, y el razonamiento explícito no mejora nada: es la única versión few-shot
   que falla un caso (`bil-04`, 2 de 3 veces).

## Hallazgos (con honestidad)

- **El held-out no discrimina.** Las 5 versiones sacan 100% en los 16 casos nuevos. Hay dos
  lecturas. La buena: la mejora de v3 no fue sobreajuste a los ejemplos, porque v3 no falla
  en casos que nunca vio. La mala: el held-out es más fácil que el principal (6 difíciles de
  16, contra 23 de 48), así que no prueba que v3 generalice mejor que v1. Para que sirva,
  hay que sumar casos difíciles de frontera.
- **El dataset principal está casi saturado.** Toda la diferencia entre versiones se
  concentra en 3 casos (`tec-10`, `bil-10`, `bil-04`). Es poca señal: con 64 casos, un caso
  que cambia mueve el accuracy ~1.6 puntos. Antes de comparar versiones nuevas conviene
  sumar casos difíciles reales, por ejemplo de tickets de producción anonimizados.
- **Exact match y juez no siempre coinciden, y eso informa.** En `tec-05` y `acc-08`, v4
  acierta la categoría pero el juez rechaza el razonamiento. El razonamiento es correcto en
  lo central, pero omite un detalle que el criterio pedía explícitamente ("nota que no hay
  pedido de reintegro"). La lección es sobre los criterios, no sobre el modelo: un criterio
  tiene que describir lo esencial, no todos los matices. Para la próxima corrida conviene
  reescribir esos dos criterios, decidiéndolo **antes** de correr, no para que v4 apruebe.
  En `bil-04` los dos coinciden: categoría errada y razonamiento rechazado.
- **Un error de infraestructura cuenta como desacierto.** En v1, `bil-01` falló una vez por
  la red: 4 intentos de conexión fallidos. Se cuenta como error a propósito: es lo que le
  pasaría a un cliente real. Si molesta para comparar prompts, el registro lo distingue
  (`error` no vacío).
- **Costo de evaluar.** Clasificar (960 ejecuciones) costó USD 0.28. El juez costó USD 0.18
  por solo 64 juicios: cada juicio de Sonnet cuesta ~10 veces una clasificación de Haiku.
  Por eso el juez sólo se usa donde aporta, en la versión con razonamiento.

## Qué ya resuelven los frameworks

Todo esto se armó a mano a propósito, para entender cada pieza. Las mismas piezas existen
empaquetadas:

| Pieza propia | En un framework |
|---|---|
| `runner.py` (casos × prompts, tabla comparativa) | **promptfoo** / **OpenAI Evals**: suites declarativas, comparación lado a lado, caché de respuestas |
| `exact_match` | Aserciones `equals` / `contains` de promptfoo |
| `juez.py` + calibración | `llm-rubric` de promptfoo; *faithfulness* y *answer relevancy* de **RAGAS** (versiones formalizadas del mismo juicio) |
| `config/evals.yaml` (umbrales) | Umbrales por aserción y `--fail-on` para CI |

Si el proyecto crece, el paso natural es promptfoo, que tiene el mismo flujo y la
infraestructura resuelta. Antes de migrar hay que fijar tres cosas: qué modelo y qué prompt
usa como juez por defecto, cómo pondera cada métrica, y qué cuenta como "coincidencia".

## Próximos pasos (fuera del alcance de M1)

- Sumar casos difíciles al held-out y reescribir los criterios de `tec-05` y `acc-08`.
- Regresión contra la corrida anterior (no sólo un umbral absoluto), con un margen que
  absorba el ruido de muestreo.
- Que otra persona revise los veredictos humanos de la calibración del juez y sume casos.
- Correr la suite con OpenAI cuando haya `OPENAI_API_KEY`, para tener la misma tabla por
  proveedor.
