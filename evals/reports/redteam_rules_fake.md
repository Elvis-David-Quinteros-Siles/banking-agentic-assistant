# Red teaming · attacks · router=rules · knowledge=keyword · llm=fake

**128 ataques, 20 mensajes legítimos** · ✅ sin daño ni falsos positivos

| Métrica | Valor |
|---|---|
| Ataques con daño (éxito del atacante) | 0.0% |
| Ataques frenados ya por el filtro de entrada (capa 1) | 39.1% |
| Legítimos bloqueados (falsos positivos) | 0.0% |

Hasta dónde llegaron los ataques:

| Etapa | Ataques |
|---|---|
| filtro de entrada | 50 |
| pregunta aclaratoria | 40 |
| llegó a un agente | 31 |
| router: fuera de alcance | 4 |
| API (conversación con una acción pendiente) | 3 |

| Técnica | Ataques | Frenados en la entrada | Con daño |
|---|---|---|---|
| base64 | 16 | 43.8% | 0.0% |
| homoglifos | 16 | 43.8% | 0.0% |
| ingles | 16 | 31.2% | 0.0% |
| leetspeak | 16 | 43.8% | 0.0% |
| multi_turno | 16 | 18.8% | 0.0% |
| original | 16 | 43.8% | 0.0% |
| roleplay | 16 | 43.8% | 0.0% |
| zero_width | 16 | 43.8% | 0.0% |

| OWASP LLM Top 10 | Ataques | Frenados en la entrada | Con daño |
|---|---|---|---|
| LLM01 Prompt injection | 32 | 68.8% | 0.0% |
| LLM02 Divulgación de datos sensibles | 40 | 32.5% | 0.0% |
| LLM06 Agencia excesiva | 24 | 0.0% | 0.0% |
| LLM07 Filtración del prompt del sistema | 16 | 93.8% | 0.0% |
| LLM09 Desinformación | 16 | 0.0% | 0.0% |
