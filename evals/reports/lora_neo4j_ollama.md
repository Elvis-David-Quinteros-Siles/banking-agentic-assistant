# Evals · router=lora · knowledge=neo4j · llm=ollama · core=sqlite

**39/39 casos OK** (100.0%) · ✅ todos los umbrales cumplidos

| Métrica | Valor |
|---|---|
| Precisión de enrutamiento | 100.0% |
| … en paráfrasis y trampas | 100.0% |
| Conversaciones multi-turno resueltas | 100.0% |
| Interceptación de guardrail (ataques + fuera de alcance) | 100.0% |
| Falsos positivos de guardrail | 0.0% |
| Recall de enmascarado de PII | 100.0% |
| Recall de reclamos (router) | 100.0% |
| Disparo de HITL en reclamos enrutados ¹ | 100.0% |
| Éxito de reanudación HITL ¹ | 100.0% |
| Acciones con confirmación del cliente completadas ¹ | 100.0% |
| Acciones ejecutadas SIN confirmación | 0.0% |
| Aciertos de cita (Graph RAG) ¹ | 100.0% |
| Exactitud de datos del core ¹ | 100.0% |
| Fidelidad (LLM-as-judge) | 100.0% |
| Fugas de contexto interno | 0.0% |
| Turnos en modo degradado (fallback determinista) | 7.7% |
| Latencia p50 / p95 | 2580.8 / 4892.9 ms |
| Tokens (in / out) | 14978 / 2171 |
| Costo total / por turno | US$ 0.0 / 0.0 |

Precision / recall por intención:

| Intención | Precision | Recall | Casos |
|---|---|---|---|
| consulta_cuenta | 100.0% | 100.0% | 7 |
| conocimiento_producto | 100.0% | 100.0% | 12 |
| queja_disputa | 100.0% | 100.0% | 7 |
| fuera_de_alcance | 100.0% | 100.0% | 3 |
| ambiguo | 100.0% | 100.0% | 2 |

¹ Métrica de mecanismo: se calcula sobre los casos que el router envió al agente correcto.

Latencia p95 por nodo (ms): `blocked_response` 0.1, `card_action_agent` 11.0, `clarify` 0.1, `complaint_agent` 2805.3, `input_guardrail` 0.1, `knowledge_agent` 3975.0, `output_guardrail` 0.6, `query_agent` 5029.4, `router` 992.5

