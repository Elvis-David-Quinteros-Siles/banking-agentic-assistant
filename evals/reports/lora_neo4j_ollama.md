# Evals · router=lora · knowledge=neo4j · llm=ollama

**31/31 casos OK** (100.0%) · ✅ todos los umbrales cumplidos

| Métrica | Valor |
|---|---|
| Precisión de enrutamiento | 100.0% |
| … en paráfrasis y trampas | 100.0% |
| Interceptación de guardrail (ataques + fuera de alcance) | 100.0% |
| Falsos positivos de guardrail | 0.0% |
| Recall de enmascarado de PII | 100.0% |
| Recall de reclamos (router) | 100.0% |
| Disparo de HITL en reclamos enrutados ¹ | 100.0% |
| Éxito de reanudación HITL ¹ | 100.0% |
| Aciertos de cita (Graph RAG) ¹ | 100.0% |
| Exactitud de datos del core ¹ | 100.0% |
| Fidelidad (LLM-as-judge) | 100.0% |
| Fugas de contexto interno | 0.0% |
| Turnos en modo degradado (fallback determinista) | 6.5% |
| Latencia p50 / p95 | 2093.0 / 3636.9 ms |
| Tokens (in / out) | 11168 / 1617 |
| Costo total / por turno | US$ 0.0 / 0.0 |

Precision / recall por intención:

| Intención | Precision | Recall | Casos |
|---|---|---|---|
| consulta_cuenta | 100.0% | 100.0% | 6 |
| conocimiento_producto | 100.0% | 100.0% | 8 |
| queja_disputa | 100.0% | 100.0% | 7 |
| fuera_de_alcance | 100.0% | 100.0% | 3 |
| ambiguo | 100.0% | 100.0% | 2 |

¹ Métrica de mecanismo: se calcula sobre los casos que el router envió al agente correcto.

Latencia p95 por nodo (ms): `blocked_response` 0.1, `clarify` 0.1, `complaint_agent` 3108.4, `input_guardrail` 0.1, `knowledge_agent` 2699.1, `output_guardrail` 0.3, `query_agent` 3001.0, `router` 901.9

