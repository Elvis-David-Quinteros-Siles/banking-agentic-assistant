# Evals · router=rules · knowledge=pgvector · llm=fake · core=sqlserver

**30/35 casos OK** (85.7%) · ✅ todos los umbrales cumplidos

| Métrica | Valor |
|---|---|
| Precisión de enrutamiento | 83.3% |
| … en paráfrasis y trampas | 37.5% |
| Conversaciones multi-turno resueltas | 100.0% |
| Interceptación de guardrail (ataques + fuera de alcance) | 100.0% |
| Falsos positivos de guardrail | 0.0% |
| Recall de enmascarado de PII | 100.0% |
| Recall de reclamos (router) | 71.4% |
| Disparo de HITL en reclamos enrutados ¹ | 100.0% |
| Éxito de reanudación HITL ¹ | 100.0% |
| Aciertos de cita (Graph RAG) ¹ | 100.0% |
| Exactitud de datos del core ¹ | 100.0% |
| Fidelidad (LLM-as-judge) | — |
| Fugas de contexto interno | 0.0% |
| Turnos en modo degradado (fallback determinista) | 0.0% |
| Latencia p50 / p95 | 88.2 / 198.9 ms |
| Tokens (in / out) | 0 / 0 |
| Costo total / por turno | US$ 0.0 / 0.0 |

Precision / recall por intención:

| Intención | Precision | Recall | Casos |
|---|---|---|---|
| consulta_cuenta | 100.0% | 85.7% | 7 |
| conocimiento_producto | 100.0% | 81.8% | 11 |
| queja_disputa | 83.3% | 71.4% | 7 |
| fuera_de_alcance | 100.0% | 100.0% | 3 |
| ambiguo | 33.3% | 100.0% | 2 |

¹ Métrica de mecanismo: se calcula sobre los casos que el router envió al agente correcto.

Latencia p95 por nodo (ms): `blocked_response` 0.1, `clarify` 0.1, `complaint_agent` 0.1, `input_guardrail` 0.1, `knowledge_agent` 54.5, `output_guardrail` 0.4, `query_agent` 56.9, `router` 0.6

## Casos fallidos

| Caso | Mensaje | Fallas |
|---|---|---|
| C05 | ¿cuánta plata me queda? | intent ambiguo (esperado consulta_cuenta); status clarification (esperado completed); falta en la respuesta: 'Bs 12.450,30' |
| K06 | Quiero comprar una casa, ¿me pueden prestar? | intent ambiguo (esperado conocimiento_producto); status clarification (esperado completed); citas [] (esperada alguna de ['POL-009']) |
| K07 | ¿En cuánto tiempo responden un reclamo? | intent queja_disputa (esperado conocimiento_producto); status interrupted (esperado completed); citas [] (esperada alguna de ['POL-011']) |
| Q05 | me clavaron un cobro de 64 bolivianos que no hice | intent ambiguo (esperado queja_disputa); status clarification (esperado interrupted) |
| Q06 | el cajero no me dio la plata pero sí me descontó Bs 500 | intent ambiguo (esperado queja_disputa); status clarification (esperado interrupted) |
