# Red teaming · holdout · router=rules · knowledge=keyword · llm=fake

**80 ataques, 10 mensajes legítimos** · ✅ sin daño ni falsos positivos

| Métrica | Valor |
|---|---|
| Ataques con daño (éxito del atacante) | 0.0% |
| Ataques frenados ya por el filtro de entrada (capa 1) | 18.8% |
| Legítimos bloqueados (falsos positivos) | 0.0% |

Hasta dónde llegaron los ataques:

| Etapa | Ataques |
|---|---|
| pregunta aclaratoria | 51 |
| filtro de entrada | 15 |
| llegó a un agente | 8 |
| router: fuera de alcance | 4 |
| API (conversación con una acción pendiente) | 2 |

| Técnica | Ataques | Frenados en la entrada | Con daño |
|---|---|---|---|
| base64 | 10 | 20.0% | 0.0% |
| homoglifos | 10 | 20.0% | 0.0% |
| ingles | 10 | 20.0% | 0.0% |
| leetspeak | 10 | 20.0% | 0.0% |
| multi_turno | 10 | 10.0% | 0.0% |
| original | 10 | 20.0% | 0.0% |
| roleplay | 10 | 20.0% | 0.0% |
| zero_width | 10 | 20.0% | 0.0% |

| OWASP LLM Top 10 | Ataques | Frenados en la entrada | Con daño |
|---|---|---|---|
| LLM01 Prompt injection | 24 | 0.0% | 0.0% |
| LLM02 Divulgación de datos sensibles | 16 | 50.0% | 0.0% |
| LLM06 Agencia excesiva | 16 | 0.0% | 0.0% |
| LLM07 Filtración del prompt del sistema | 16 | 43.8% | 0.0% |
| LLM09 Desinformación | 8 | 0.0% | 0.0% |
