# Servidor MCP

`backend/app/mcp_server.py` expone el core bancario, la base de conocimiento y el sistema
multi-agente completo por **Model Context Protocol** (SDK oficial `mcp`, FastMCP). Cualquier host
MCP (Claude Code, Claude Desktop, un IDE u otro agente) puede usarlos sin integración a medida.

## Qué expone

| Tipo | Nombre | Qué hace |
|---|---|---|
| Tool | `get_accounts` | Cuentas y saldos del cliente de la sesión (número enmascarado) |
| Tool | `get_transactions(account, limit)` | Últimos movimientos de una cuenta **del cliente** (por últimos 4 dígitos) |
| Tool | `search_policies(question, product?, k)` | Graph RAG sobre políticas y fichas; fragmentos con id para citar |
| Resource | `policy://{doc_id}` | Texto completo de una política (p. ej. `policy://POL-004`) |
| Tool | `list_cards` | Tarjetas del cliente y su estado |
| Tool | `prepare_card_block(last4, reason)` | Paso 1: prepara el bloqueo y devuelve un código. **No bloquea** |
| Tool | `confirm_card_block(confirmation_code)` | Paso 2: ejecuta el bloqueo (código de un solo uso, vence en 5 min) |
| Tool | `ask_assistant(message)` | El grafo completo (guardrails → router → agentes → verificación), con memoria entre llamadas |

## Las mismas garantías que el chat

- **Identidad fijada en el servidor.** Ninguna herramienta recibe `customer_id` (hay un test que lo
  verifica en el esquema de cada tool). Con `AUTH_MODE=jwt` (por defecto) el cliente sale de
  `MCP_ACCESS_TOKEN`, verificado igual que en la API (firma, vencimiento, emisor, rol `customer`);
  `MCP_CUSTOMER_ID` solo vale con `AUTH_MODE=off`. El modelo del host no puede pedir datos de otro cliente.
- **Guardrails antes de buscar.** `search_policies` bloquea prompt injection y referencias a otros
  clientes, y enmascara PII antes de que el texto llegue al retriever.
- **Acciones en dos pasos.** Un solo tool call no puede bloquear una tarjeta: el host tiene que
  mostrar `confirmation_prompt` al usuario y volver con el código. Es el equivalente MCP del
  `interrupt()` + `/confirm` del grafo. La ejecución pasa por el mismo repositorio
  (`usp_BlockCard` en SQL Server: pertenencia, idempotencia y auditoría).

## Conectarlo

Claude Code (desde la raíz del repo):

```bash
# 1) Token del cliente: la API y el servidor MCP deben compartir JWT_SECRET (o el mismo IdP OIDC)
#    POST http://localhost:8000/auth/demo-token  {"customer_id": "CLI-001"}  → access_token
# 2) Registrar el servidor con ese token
claude mcp add banco --env JWT_SECRET=<el-de-la-api> --env MCP_ACCESS_TOKEN=<access_token> \
  -- .venv/Scripts/python.exe -m app.mcp_server
# El servidor se ejecuta con cwd = backend/ (o PYTHONPATH=backend)
# Desarrollo sin tokens: --env AUTH_MODE=off --env MCP_CUSTOMER_ID=CLI-001
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "banco": {
      "command": "C:\\dtic\\banking-agentic-assistant\\.venv\\Scripts\\python.exe",
      "args": ["-m", "app.mcp_server"],
      "cwd": "C:\\dtic\\banking-agentic-assistant\\backend",
      "env": { "JWT_SECRET": "<el-de-la-api>", "MCP_ACCESS_TOKEN": "<token del cliente>" }
    }
  }
}
```

Por HTTP (streamable HTTP, para hosts remotos): `python -m app.mcp_server --http --port 8765`
→ `http://localhost:8765/mcp`. Expuesto fuera de la máquina, necesitaría autenticación OAuth
(la especificación de MCP la define para el transporte HTTP); no se publica en la demo.

Los backends son los mismos que los de la API: `CORE_BACKEND=sqlserver`, `KNOWLEDGE_BACKEND=neo4j`, etc.

## Tests

`backend/tests/test_mcp_server.py` usa un **cliente MCP real** (sesión en memoria del SDK):
lista tools y resources, verifica que ninguna tool acepte `customer_id`, que una cuenta ajena no se
devuelva, que la búsqueda bloquee injection y enmascare PII, que el bloqueo exija los dos pasos
(y que el código sea de un solo uso), y que `ask_assistant` recuerde el turno anterior.
