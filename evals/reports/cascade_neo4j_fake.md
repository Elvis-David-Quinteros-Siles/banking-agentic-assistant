# Evals · router=cascade · knowledge=neo4j · llm=fake · core=sqlite

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
| Fidelidad (LLM-as-judge) | — |
| Fugas de contexto interno | 0.0% |
| Turnos en modo degradado (fallback determinista) | 0.0% |
| Turnos clasificados por reglas (sin modelo) | 35.3% |
| Latencia p50 / p95 | 748.6 / 1325.2 ms |
| Tokens (in / out) | 3528 / 549 |
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

Latencia p95 por nodo (ms): `blocked_response` 0.1, `card_action_agent` 34.0, `clarify` 0.1, `complaint_agent` 0.2, `input_guardrail` 0.1, `knowledge_agent` 389.5, `output_guardrail` 0.5, `query_agent` 13.2, `router` 985.6

