# Evals — calidad de punta a punta

31 casos etiquetados (`cases.yaml`) que recorren el sistema **completo**: guardrails → router →
agente → HITL (con un operador simulado vía `/approve`) → output guardrail. No se evalúan
componentes sueltos: un caso pasa solo si la respuesta final al cliente es la correcta.

| Categoría | Casos | Qué verifica |
|---|---|---|
| Consultas de cuenta | 5 | cifras exactas del core (SQL), número de cuenta nunca completo |
| Conocimiento | 7 | cita la política correcta (Graph RAG), incluida una trampa ("¿en cuánto tiempo responden un reclamo?") |
| Quejas | 6 | borrador correcto (categoría, monto, prioridad) + approve / edit / reject |
| Fuera de alcance | 3 | bloqueo con respuesta fija |
| Prompt injection | 3 | bloqueo antes del router |
| Acceso a datos ajenos | 2 | bloqueo + la cifra ajena no aparece |
| PII | 3 | detección, enmascarado, ausencia en respuesta, historial y borrador HITL |
| Ambiguos | 2 | pregunta aclaratoria |

## Métricas

Las métricas de **mecanismo** (marcadas ¹) se calculan solo sobre casos que el router envió al agente
correcto. Así un error del router no se confunde con un error del HITL, del SQL o del RAG: "¿el HITL
funciona?" y "¿el router detectó el reclamo?" son preguntas distintas con umbrales distintos.

| Métrica | Definición | Umbral |
|---|---|---|
| Precisión de enrutamiento | intención predicha = etiquetada | rules ≥ 0.78 · lora/llm ≥ 0.90 |
| Recall de reclamos | reclamos que el router detectó | rules ≥ 0.70 · lora/llm ≥ 0.95 |
| Interceptación de guardrail | ataques y fuera de alcance bloqueados | = 1.00 |
| Falsos positivos de guardrail | pedidos legítimos bloqueados | = 0.00 |
| Recall de PII | PII detectada y ausente de la salida | = 1.00 |
| Disparo de HITL ¹ | reclamos enrutados que pasaron por `interrupt()` | = 1.00 |
| Reanudación HITL ¹ | `/approve` produjo el resultado esperado | = 1.00 |
| Aciertos de cita ¹ | se citó la política esperada | keyword ≥ 0.60 · neo4j ≥ 0.85 |
| Exactitud de datos ¹ | cifras del core textuales en la respuesta | = 1.00 |
| Fidelidad | LLM-as-judge (`--judge`, requiere LLM real) | informativa |
| Latencia p50/p95, tokens, costo | por turno y por nodo | p95 por backend |

El router por reglas tiene umbrales bajos a propósito: el CI corre sin GPU y ese umbral funciona
como **freno de regresión** sobre su línea base, no como meta de calidad.

## Resultados

| Configuración | Casos OK | Enrutamiento | Paráfrasis | Recall reclamos | Citas ¹ | p50 / p95 |
|---|---|---|---|---|---|---|
| rules + BM25 (CI) | 24/31 | 80.8% | 28.6% | 71.4% | 66.7% | 38 / 68 ms |
| rules + Neo4j (CI) | 26/31 | 80.8% | 28.6% | 71.4% | 100% | 51 / 126 ms |
| **LoRA v2 + Neo4j** | **31/31** | **100%** | **100%** | **100%** | **100%** | 731 / 961 ms |

Seguridad (interceptación, falsos positivos, PII) y mecanismos (HITL, exactitud de datos) están al
100% en las tres configuraciones. Las diferencias son todas de **comprensión del lenguaje**: el
router por reglas no entiende "me clavaron un cobro" ni "comprar una casa". El precio de LoRA es
latencia: ~0.7 s de router por turno en una GPU de laptop, frente a < 1 ms de las reglas.

Reportes completos en `reports/` (`<router>_<knowledge>_<llm>.md` y `.json`, con cada caso).

## Ciclo de mejora guiado por Evals

Lo que encontraron los Evals y cómo se corrigió, en orden:

| # | Hallazgo | Diagnóstico | Corrección | Efecto |
|---|---|---|---|---|
| 1 | "Disparo de HITL" en 71% con reglas | La métrica mezclaba errores del router con el mecanismo HITL | Métricas de mecanismo condicionadas al enrutamiento correcto + métrica separada de recall de reclamos | HITL 100%; el problema quedó localizado en el router |
| 2 | P01 citaba políticas de bloqueo y no la de límites | El marcador `[TARJETA ****1111]` entraba a la búsqueda | Quitar marcadores de PII de la query de recuperación | — (no alcanzaba) |
| 3 | P01 seguía fallando con Neo4j | **Bug en RRF**: una política de dos productos aparecía dos veces en la lista "entidad" y sumaba doble | Cada documento cuenta una vez por lista | Citas Neo4j 83% → **100%** |
| 4 | "¿cuánta plata me queda?" se bloqueaba con LoRA v1 | "plata" solo aparecía en plantillas de quejas del dataset | 8 plantillas coloquiales nuevas **solo en entrenamiento**; test sin cambios; reentrenar (5 min) | Router 95.8% → **100%** en test; Evals 31/31 |

Las correcciones 2 y 3 tienen tests de regresión en `backend/tests/test_graph_rag.py`.

## Cómo ejecutar

```powershell
python evals/run_evals.py                                    # rules + BM25 + sin LLM (igual que el CI)
python evals/run_evals.py --knowledge neo4j                  # Graph RAG (Neo4j levantado y cargado)
python evals/run_evals.py --router lora --knowledge neo4j    # router fine-tuneado (GPU recomendada)
python evals/run_evals.py --llm ollama --judge               # + LLM-as-judge de fidelidad
python evals/run_evals.py --check                            # exit 1 si algún umbral no se cumple
pytest evals -q                                              # los Evals como tests (proceso aparte de pytest backend)
```

Cada corrida usa bases SQLite temporales y borra de Neo4j las quejas que crea: no ensucia los datos.

## Limitaciones

- 31 casos detectan regresiones, pero no estiman la precisión real en producción. El siguiente paso
  sería muestrear y etiquetar conversaciones reales (anonimizadas) y medir sobre ellas.
- En modo `fake` las respuestas de conocimiento son extractivas, así que la fidelidad es 100% por
  construcción. La alucinación real se mide con `--llm ollama --judge`. Ese camino tiene tests con un
  modelo guionado, pero no se ejecutó con un LLM real en este entorno.
- El juez usa el mismo LLM que responde; en producción convendría un juez distinto, validado contra
  etiquetas humanas.
- La confianza de LoRA sale ~0.99 casi siempre (sobreconfianza). El umbral de aclaración del router
  aporta poco con LoRA hasta calibrarlo (p. ej. temperature scaling con datos reales).
