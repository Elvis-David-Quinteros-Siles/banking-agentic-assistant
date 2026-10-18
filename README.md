# Asistente bancario agéntico

[![CI](https://github.com/Elvis-David-Quinteros-Siles/banking-agentic-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Elvis-David-Quinteros-Siles/banking-agentic-assistant/actions/workflows/ci.yml)
![Cobertura](https://img.shields.io/badge/cobertura-82%25%20(m%C3%ADn.%2080%25%20en%20CI)-brightgreen)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![mypy](https://img.shields.io/badge/tipos-mypy-blue)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-purple)
![Neo4j](https://img.shields.io/badge/Neo4j-5.26-green)
![React](https://img.shields.io/badge/React-18-61dafb)

Sistema multi-agente de **atención al cliente bancaria**: responde saldos y movimientos desde el
core (SQL), explica productos y políticas con **Graph RAG** sobre Neo4j, gestiona reclamos con
**aprobación humana (HITL)** y bloquea fuera de alcance, prompt injection y fugas de PII. El router
es un **Qwen2.5-0.5B ajustado con LoRA** que corre on-premise, y todo el sistema se mide con
**Evals de punta a punta** que corren en CI.

**[▶ Probar la demo en vivo](https://banking-agentic-assistant.edqs.online)** · [dashboard de KPIs](https://banking-agentic-assistant.edqs.online/#/kpis) · [cola del operador](https://banking-agentic-assistant.edqs.online/#/operador) · [ver directamente un reclamo con aprobación humana](https://banking-agentic-assistant.edqs.online/?q=Me%20cobraron%20Bs%20350%20dos%20veces%20en%20la%20tarjeta%20de%20d%C3%A9bito)

<sub>La demo pública corre en modo liviano (router por reglas, búsqueda BM25, respuestas por plantilla) porque no requiere GPU. Los resultados con router LoRA, Graph RAG y LLM real están más abajo y en el GIF.</sub>

![Demo: saldo, paráfrasis resuelta con Graph RAG, reclamo aprobado por el operador y ataque bloqueado](docs/img/demo.gif)

<sub>Recorrido real de 45 s con router LoRA + Graph RAG + qwen2.5:3b en Ollama: saldo, una paráfrasis sin palabras clave, un reclamo que el operador edita y aprueba, y un prompt injection bloqueado. [Video en MP4](docs/img/demo.mp4).</sub>

> Todos los clientes, cuentas, productos y políticas son **ficticios**. No representan la oferta ni la
> normativa de ninguna entidad financiera real.

## Resultados

| | Reglas + BM25 (baseline, CI) | LoRA + Graph RAG | **LoRA + Graph RAG + LLM real** |
|---|---|---|---|
| Evals de punta a punta | 28/35 | **35/35** | **31/31** (2 corridas) ¹ |
| Precisión de enrutamiento · en paráfrasis | 83.3% · 37.5% | 100% · 100% | **100% · 100%** |
| Conversaciones multi-turno (seguimientos y cambio de tema) | 100% | **100%** | — ¹ |
| Reclamos detectados (si no se detecta, no pasa por HITL) | 71.4% | 100% | **100%** |
| Citas a la política correcta | 77.8% | 100% | **100%** |
| Fidelidad (LLM-as-judge) · fugas de contexto | — | — | **100% · 0%** |
| Seguridad: ataques bloqueados · falsos positivos · PII | 100% · 0% · 100% | 100% · 0% · 100% | **100% · 0% · 100%** |
| Latencia p50 / p95 por turno | 99 / 252 ms | 770 / 1024 ms | 1.8–2.1 / 2.7–3.6 s |

¹ La corrida con LLM real se hizo con los 31 casos previos a los 4 de multi-turno.
LLM real = qwen2.5:3b en Ollama (Docker, GPU de 4 GB, 100% on-premise). Las respuestas de los agentes
las redacta el LLM; la columna del medio usa redacción determinista.

- **Fine-tuning**: LoRA sobre Qwen2.5-0.5B, 5.6 min en una GPU de laptop (4 GB). Precisión en
  frases no vistas: **100%**, frente a 59.2% de las reglas y **36.7% del mismo modelo sin ajustar**.
- **Costo**: ~US$ 12 por 100 000 turnos con un LLM cloud económico; −29% con el router local. Ver
  [Cost & Latency](docs/COST_LATENCY.md).
- **Los Evals encontraron 14 problemas reales**, todos corregidos (los de código, con test de regresión):
  4 con redacción determinista (entre ellos un bug de fusión RRF) y 6 al pasar a un LLM real (un
  ReAct que pedía el número de cuenta, citas mal atribuidas, extractos parafraseados, el contexto
  interno filtrado al cliente...). Primera medición con LLM: 28/31; final: 31/31.
  Ver [ciclo de mejora](evals/README.md#ciclo-de-mejora-guiado-por-evals).

---

## El problema de negocio

Un call center bancario recibe tres tipos de consulta que exigen tratamientos opuestos:

| Necesidad | Riesgo si se automatiza mal | Tratamiento en este sistema | KPI |
|---|---|---|---|
| "¿Cuánto tengo?", "mis movimientos" | Dato incorrecto o de **otro cliente** | Agente ReAct con SQL parametrizado; el `customer_id` sale de la sesión, nunca del LLM | Exactitud de datos 100% |
| "¿Qué comisión cobran?", "¿qué necesito para un crédito?" | **Alucinar** una tasa o un requisito | Graph RAG con citas obligatorias, abstención y verificación de cifras | Citas correctas 100% |
| "Me cobraron dos veces", "no reconozco esta compra" | Registrar mal un reclamo (riesgo regulatorio) | El agente arma el borrador; **un operador aprueba, edita o rechaza** | 100% de reclamos pasan por HITL |
| Temas ajenos, ataques, datos sensibles | Fuga de PII, manipulación del asistente | Guardrails deterministas antes y después del LLM | 0 fugas, 0 falsos positivos |

## Arquitectura

```mermaid
flowchart TD
    U([Cliente]) --> API[FastAPI<br/>/chat · /chat/stream SSE]
    API --> GIN[input_guardrail<br/>PII · injection · datos ajenos]
    GIN -- bloqueado --> BLK[respuesta fija]
    GIN --> R[router<br/>Qwen2.5-0.5B + LoRA · fallback reglas]
    R -- consulta --> QA[query_agent<br/>ReAct + SQL parametrizado]
    R -- conocimiento --> KA[knowledge_agent<br/>Graph RAG Neo4j]
    R -- reclamo --> CA[complaint_agent<br/>borrador estructurado]
    R -- fuera de alcance --> BLK
    R -- ambiguo --> CL[pregunta aclaratoria]
    CA --> H{{hitl_review<br/>interrupt}}
    OP([Operador]) -- /approve --> H
    H -- aprobar / editar --> REG[registrar<br/>SQLite + Neo4j]
    H -- rechazar --> REJ[respuesta al cliente]
    QA & KA & REG & REJ --> GOUT[output_guardrail<br/>cifras vs contexto · enmascarado]
    GOUT --> U
    QA -.-> SQL[(SQL Server<br/>o SQLite)]
    KA -.-> NEO[(Neo4j o pgvector<br/>vector + full-text + relaciones)]
```

| Componente | Implementación | Detalle |
|---|---|---|
| Orquestación | LangGraph `StateGraph`, checkpointer SQLite, `interrupt()` + `Command(resume=…)` | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Router | Qwen2.5-0.5B + LoRA (JSON: intención + producto + monto + moneda), con fallback a reglas | [finetune/](finetune/README.md) |
| Consultas | Loop ReAct escrito a mano (máx. 4 vueltas), tools SQL con el cliente fijado por sesión | `backend/app/graph/nodes/query_agent.py` |
| Conocimiento | Recuperación híbrida: 2 índices vectoriales + full-text en español + entidad + expansión `(Producto)-[:TIENE_POLITICA]->(Politica)`, fusionados con RRF | `backend/app/rag/neo4j_retriever.py` |
| HITL | El grafo se pausa; el estado sobrevive entre requests y reinicios; approve / edit / reject. **Back-office** (`#/operador`) con la cola ordenada por prioridad y espera; token de operador opcional | `backend/app/graph/nodes/hitl.py`, `backend/app/api/routes_operator.py` |
| Guardrails | PII (tarjeta con Luhn, CI boliviano, celular, email), injection, acceso ajeno, grounding de cifras | `backend/app/guardrails/` |
| API | `/chat`, `/chat/stream` (SSE), `/approve/{thread_id}`, `/operator/queue`, `/metrics/summary`, `/health` | `backend/app/api/` |
| Analítica | Métricas por turno (intención, estado, latencia por nodo, tokens, costo) y cola HITL; el cliente se guarda seudonimizado y nunca el texto | `backend/app/analytics/` |
| Frontend | React 18 + Vite + TypeScript: streaming, recorrido del grafo en vivo, tarjeta HITL | [frontend/](frontend/README.md) |
| Evals + CI | 35 casos (incluye multi-turno), umbrales por métrica; CI con calidad (ruff + mypy), cobertura mínima, Neo4j, SQL Server y pgvector como servicios | [evals/](evals/README.md) |

**Degradación controlada**: si el LLM, el modelo LoRA o Neo4j fallan, cada nodo usa su ruta
determinista (reglas, BM25 en memoria, respuesta extractiva) y la respuesta lleva `degraded: true`.
El sistema empeora en redacción, no en exactitud ni seguridad.

## Competencias que demuestra

| Competencia | Dónde se ve |
|---|---|
| Traducir necesidades de negocio en flujos agénticos | Tabla de negocio de arriba → nodos del grafo, cada uno con su KPI |
| Flujos agénticos complejos | Grafo de 11 nodos con aristas condicionales, ReAct, HITL con pausa y reanudación |
| KPIs por caso de uso y monitoreo | **Dashboard en vivo** (`#/kpis`): contención, latencia p50/p95, degradación, bloqueos, costo y, por intención, % resuelto / a HITL / aclaración; HITL: aprobados sin editar y tiempo de decisión del operador. Sin guardar texto de clientes |
| Optimizar agentes en producción | [Ciclo de mejora](evals/README.md#ciclo-de-mejora-guiado-por-evals): falla detectada → diagnóstico → corrección → re-medición |
| Evals: precisión/recall, alucinación, KPIs | `evals/`: precision/recall por intención, grounding de cifras, LLM-as-judge opcional, umbrales en CI |
| LLMs, prompt y context engineering | Prompts cortos por nodo, presupuesto de contexto (top-4, 6 mensajes), vecinos del grafo en el contexto |
| Fine-tuning | LoRA/QLoRA, dataset sintético con split por plantilla, comparación con el modelo base |
| Guardrails y HITL | Entrada y salida deterministas; HITL obligatorio en reclamos, con edición del operador |
| SQL (SQL Server on-premise) | **SQL Server 2022** en Docker: procedimientos almacenados que validan la pertenencia de cada cuenta y un login de aplicación con **mínimo privilegio** (sin `SELECT` sobre tablas). SQLite para desarrollo y CI, detrás del mismo repositorio |
| Hybrid retrieval, vector DB (pgvector), multi-agente, ReAct | Recuperación híbrida en **Neo4j y en pgvector** (HNSW + tsvector + RRF), comparadas con los Evals; router + 3 subagentes; ReAct propio |
| Cost & latency awareness | [docs/COST_LATENCY.md](docs/COST_LATENCY.md) con cifras medidas |

## Cómo ejecutarlo

Requisitos: Python 3.11+, Node 20+. Opcional: Docker (Neo4j), GPU NVIDIA (router LoRA), Ollama.

**Atajo en Windows**: con el entorno ya instalado, un solo comando levanta todo, verifica cada pieza
(descarga el modelo de Ollama o carga el grafo si faltan) y abre el navegador:

```powershell
.\start-demo.ps1            # completo: Neo4j + Ollama + router LoRA + frontend
.\start-demo.ps1 -Lite      # sin Docker ni GPU
.\start-demo.ps1 -Stop      # detiene servidores, ventanas y contenedores de la demo
```

**Demo pública**: el `Dockerfile` de la raíz empaqueta frontend + API en modo liviano (~220 MB, sin GPU
ni servicios externos) para publicarla gratis en Render o Hugging Face Spaces: ver [docs/DEPLOY.md](docs/DEPLOY.md).

Los pasos manuales equivalentes son los siguientes.

### 1. Mínimo: sin Docker, sin GPU, sin API keys (unos 2 minutos)

```powershell
git clone https://github.com/Elvis-David-Quinteros-Siles/banking-agentic-assistant.git
cd banking-agentic-assistant
python -m venv .venv; .\.venv\Scripts\Activate.ps1        # Linux/macOS: source .venv/bin/activate
pip install -r backend/requirements.txt

cd backend; uvicorn app.main:app --port 8000               # terminal 1
cd frontend; npm install; npm run dev                      # terminal 2 → http://localhost:5173
```

Usa router por reglas, BM25 en memoria y ningún LLM. SQLite se crea y se carga solo al arrancar.

### 2. Con Graph RAG (Neo4j)

```powershell
docker compose up -d neo4j
cd backend; python scripts/seed_neo4j.py                   # la primera vez descarga el modelo de embeddings
$env:KNOWLEDGE_BACKEND="neo4j"; uvicorn app.main:app --port 8000
```

Neo4j Browser: http://localhost:7474 (`neo4j` / `bankdemo123`). Prueba:
`MATCH path = (c:Cliente {cliente_id:'CLI-001'})-[*1..2]-() RETURN path`

### 3. Con el router LoRA (GPU recomendada)

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cu126   # primero torch con CUDA
pip install -r finetune/requirements.txt
python finetune/train_lora.py                              # ~6 min en una GPU de 4 GB
cd backend; $env:ROUTER_BACKEND="lora"; $env:HF_HUB_OFFLINE="1"; uvicorn app.main:app --port 8000
```

### 4. Con un LLM on-premise para redactar (Ollama en Docker)

```powershell
docker compose up -d ollama                                # usa la GPU NVIDIA si Docker la expone
docker exec bank-ollama ollama pull qwen2.5:3b             # ~1.9 GB
cd backend; $env:LLM_PROVIDER="ollama"; uvicorn app.main:app --port 8000
```

También acepta `openai` o `anthropic` con API key (ver `.env.example`). Sin LLM, los agentes
responden con plantillas y extractos citados.

### 5. Con el stack empresarial: SQL Server + pgvector

```powershell
docker compose up -d sqlserver pgvector
cd backend
python scripts/init_sqlserver.py     # base, procedimientos, login de aplicación (mínimo privilegio) y datos
python scripts/seed_pgvector.py      # productos y políticas con embeddings (HNSW) y tsvector en español
$env:CORE_BACKEND="sqlserver"; $env:KNOWLEDGE_BACKEND="pgvector"; uvicorn app.main:app --port 8000
```

Se combinan con lo anterior (por ejemplo `ROUTER_BACKEND=lora` y `LLM_PROVIDER=ollama`). Comparación
Neo4j vs pgvector en [evals/README.md](evals/README.md#neo4j-frente-a-pgvector-mismo-algoritmo-híbrido-mismos-embeddings).

### Tests y Evals

```powershell
pytest backend -q                                          # 98 tests (los de integración se saltan si falta el servicio)
python evals/run_evals.py --check                          # Evals de punta a punta con umbrales
python evals/run_evals.py --router lora --knowledge neo4j  # router LoRA + Graph RAG: 35/35
python evals/run_evals.py --router lora --knowledge neo4j --llm ollama --judge   # + LLM real y juez: 31/31
cd frontend; npm test; npm run build
python frontend/e2e/smoke_test.py                          # E2E con clics reales (Playwright + Edge/Chrome)
python frontend/e2e/record_demo.py                         # regraba docs/img/demo.gif y demo.mp4
```

Demo por consola: `python backend/scripts/chat_cli.py --stream "¿Cuál es mi saldo?"`.

## Capturas

| Dashboard de KPIs | Recorrido del grafo en vivo y aprobación | Guardrails: PII e injection |
|---|---|---|
| ![kpis](docs/img/ui-kpis.png) | ![aprobado](docs/img/ui-approved.png) | ![guardrails](docs/img/ui-guardrails.png) |

## Decisiones de diseño

- **Sin text-to-SQL.** Las consultas usan tools con SQL parametrizado y el `customer_id` de la
  sesión: un prompt injection no puede leer cuentas ajenas, aunque pase el guardrail (hay test).
- **La PII se enmascara antes de cualquier LLM** y reemplaza al mensaje original en el historial:
  la tarjeta nunca queda persistida en el checkpoint (hay test).
- **El LLM decide y el código redacta los datos.** El LLM entiende qué pide el cliente y elige qué
  consultar (por ejemplo, la cuenta y la cantidad de movimientos); el extracto, los saldos y la
  prioridad de un reclamo los produce el código. Una cifra de la respuesta que no está en el contexto
  anula la respuesta generada. Este patrón salió de los Evals con LLM real: el modelo de 3B
  parafraseaba comercios y omitía cuentas.
- **Abstenerse es una respuesta válida**: si ningún documento supera la similitud mínima, el agente
  de conocimiento dice que no tiene esa información en vez de generar.
- **El router LoRA es on-premise** porque se ejecuta en cada turno y recibe el texto del cliente.
- **Las métricas de mecanismo y las de modelo van separadas** en los Evals, para que cada umbral
  mida una sola cosa.

## Qué haría distinto en producción

**Datos e integración**
- SQL Server con `pyodbc` + ODBC Driver 18, TLS obligatorio, pool de conexiones (hoy cada consulta abre
  una conexión: ~80 ms) y réplica de lectura. Los procedimientos y el login de mínimo privilegio ya están.
- Base de conocimiento alimentada desde la fuente oficial de políticas, con versionado y fecha de
  vigencia. Entre Neo4j y pgvector, medí calidad igual y pgvector ~2× más rápido con un salto de
  relación: elegiría pgvector si el banco ya opera PostgreSQL y Neo4j si aparecen consultas de varios saltos.
- Checkpointer en PostgreSQL o SQL Server en lugar de SQLite (concurrencia y alta disponibilidad).
- Réplica de reclamos a Neo4j vía *outbox* asíncrono: hoy es best-effort en el camino del `/approve`.

**Seguridad y gobierno**
- Autenticación OIDC del canal; `/approve` en un back-office separado con RBAC y auditoría
  (quién aprobó qué y cuándo); cola de HITL con SLA y escalamiento.
- NER (p. ej. Presidio) además de las regex de PII; PII fuera de los logs; política de retención.
- Red teaming periódico con un set de ataques más amplio que los 5 casos actuales.

**Calidad y operación**
- Evals sobre conversaciones reales anonimizadas y etiquetadas por humanos; los 35 casos sintéticos
  detectan regresiones, pero no estiman la precisión real.
- Trazas con OpenTelemetry / LangSmith / Langfuse, y los KPIs del dashboard exportados a
  Prometheus/Grafana con alertas de deriva (subida de `degraded`, de aclaraciones, de reclamos
  rechazados o de la espera en la cola HITL). La analítica iría a un data warehouse, no a SQLite.
- Calibrar la confianza del LoRA (hoy sale ~0.99 casi siempre) para que el umbral de aclaración y
  una cascada reglas → LoRA funcionen.
- Pipeline de reentrenamiento (Airflow / Metaflow): datos nuevos del monitoreo → reentrenar →
  Evals como compuerta → despliegue del adaptador versionado.
- Servir el LoRA con vLLM o llama.cpp y generar solo las claves no nulas para bajar la latencia
  del router, que hoy es el 95% del turno.
- Exponer las tools bancarias vía **MCP** para reutilizarlas desde otros agentes con un único
  punto de control de permisos.

## Limitaciones conocidas

- **LLM evaluado**: los resultados con LLM real son con qwen2.5:3b local. Otro modelo (más grande o
  cloud) necesita su propia corrida de Evals; el CI corre sin LLM porque no tiene GPU.
- **Datos sintéticos**: 600 ejemplos de entrenamiento generados con plantillas y 35 casos de Evals.
  El 100% en test mide generalización a frases nuevas del mismo dominio, no rendimiento en producción.
- **Router por reglas** (configuración del CI, sin GPU): no entiende paráfrasis; pide aclaración en
  lugar de responder mal, pero pierde el 29% de los reclamos expresados con otras palabras.
- El primer `/approve` tras arrancar puede tardar unos segundos (primera escritura en Neo4j).

## Estructura

```
backend/        FastAPI + LangGraph: api/, graph/nodes/, guardrails/, rag/, tools/, llm/ · tests/
frontend/       React + Vite: chat SSE, tarjeta HITL, recorrido del grafo · e2e/
finetune/       dataset sintético, entrenamiento LoRA/QLoRA, inferencia, evaluación del router
evals/          35 casos, umbrales, runner de punta a punta, LLM-as-judge, reportes
data/seed/      clientes, cuentas, movimientos, productos, políticas y quejas (ficticios)
docs/           ARCHITECTURE.md (diseño, Fase 0), COST_LATENCY.md, DEPLOY.md, capturas y demo.gif
Dockerfile      imagen de la demo pública (frontend + API, modo liviano)
start-demo.ps1  levanta la demo completa en Windows con un comando
.github/        CI: backend, Evals, Graph RAG con Neo4j, frontend
```

El proyecto se construyó por fases (diseño → backend → multi-agente → Graph RAG → LoRA → frontend →
Evals → documentación); cada fase es un commit en el historial.
