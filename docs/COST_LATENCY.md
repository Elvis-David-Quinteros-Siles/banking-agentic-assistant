# Cost & Latency Awareness

Todas las cifras están **medidas en este proyecto**, salvo donde se indica "supuesto".
Hardware: laptop con RTX 3050 (4 GB VRAM), 16 GB RAM, Windows 11; Neo4j 5.26 en Docker.
Fuentes: `evals/reports/*.json`, `finetune/reports/router_eval.json`, `evals/reports/cost_estimate.json`.

## 1. Dónde se va el tiempo de un turno

Latencia p95 por nodo (Evals de punta a punta, 31 casos):

| Nodo | rules + Neo4j | LoRA + Neo4j | Qué lo domina |
|---|---|---|---|
| `input_guardrail` | 0.1 ms | 0.1 ms | regex deterministas |
| `router` | **0.6 ms** | **969 ms** | reglas vs. generación de ~21 tokens con Qwen2.5-0.5B en GPU |
| `query_agent` | 2.2 ms | 32 ms | SQL parametrizado sobre SQLite |
| `knowledge_agent` | 86 ms | 106 ms | embedding de la pregunta + 4 consultas Cypher + RRF |
| `complaint_agent` | 0.1 ms | 0.2 ms | extracción determinista (sin LLM en modo fake) |
| `output_guardrail` | 0.4 ms | 0.5 ms | grounding léxico + enmascarado |
| **Turno completo p50 / p95** | **80 / 124 ms** | **731 / 961 ms** | |

Lectura: con un modelo local de 0.5B, **el router es el 95% de la latencia** del turno. Cualquier
optimización de latencia empieza ahí, no en el RAG.

Variantes del router medidas aparte (`finetune/infer.py`, 6 frases, misma máquina):

| Router | Latencia media | Precisión (test 120 frases) | Dónde corre |
|---|---|---|---|
| Reglas | < 1 ms | 59.2% | CPU |
| Qwen2.5-0.5B base | ~970 ms | 36.7% | GPU |
| Qwen2.5-0.5B + LoRA v2 | ~730 ms GPU · ~3.6 s CPU | 100% | GPU / CPU on-premise |

El modelo base sin ajustar es más lento que LoRA (genera salidas más largas y a veces JSON inválido)
y además es el menos preciso.

### Con un LLM real redactando (qwen2.5:3b en Ollama, Docker, misma GPU que el router LoRA)

`ollama ps` reporta el modelo **100% en GPU** con contexto de 2048: router LoRA (~1 GB) y LLM de 3B
(~2.1 GB) caben juntos en los 4 GB.

| Nodo | p95 (dos corridas de Evals) |
|---|---|
| `router` (LoRA) | 0.9–1.0 s |
| `query_agent` (1 llamada al LLM; el extracto lo redacta el código) | 2.9–3.0 s |
| `knowledge_agent` (Graph RAG + generación con citas) | 2.0–2.7 s |
| `complaint_agent` (borrador estructurado) | 2.1–3.1 s |
| **Turno completo p50 / p95** | **1.8–2.1 s / 2.7–3.6 s** |

Tokens medidos (no estimados): **360 de entrada y 52 de salida por turno** en promedio sobre los 31
casos (incluye los bloqueos, que no consumen tokens). Es menos que la estimación de la sección 3
(464 / 82), porque esa suponía dos vueltas de ReAct y ahora las cuentas van precargadas y el extracto
lo redacta el código, lo que ahorra una llamada.

Bajar el contexto de Ollama de 4096 (default) a 2048 no cambia la calidad (el prompt más largo ronda
600 tokens) y libera VRAM para que los dos modelos convivan en GPU.

### Motores de datos (Evals, router por reglas)

| Componente | SQLite / Neo4j | SQL Server / pgvector |
|---|---|---|
| `query_agent` p95 (core bancario) | 2 ms (SQLite, en proceso) | 80 ms (SQL Server por TCP, conexión nueva por consulta) |
| `knowledge_agent` p95 (RAG híbrido) | 105 ms (Neo4j) | 42–60 ms (pgvector, HNSW + GIN) |

Los ~80 ms de SQL Server son casi todos de abrir la conexión (login TDS) en cada llamada; con un pool
de conexiones bajarían a pocos milisegundos. En producción iría con pool y TLS.

## 2. Arranques en frío detectados y corregidos

| Síntoma | Causa | Corrección | Resultado |
|---|---|---|---|
| Primera pregunta de conocimiento: 3.1 s | Carga del modelo de embeddings en el primer request | Precarga en el `lifespan` de FastAPI | ~95 ms desde la primera pregunta |
| Primera pregunta con router LoRA lenta | Carga de pesos + compilación de kernels CUDA | `classify("hola")` de calentamiento al arrancar | sin penalización en el primer cliente |
| Primera respuesta con Ollama: **90 s** | Carga del modelo de 3B en la GPU desde el volumen de Docker | Llamada de calentamiento en el `lifespan` + `OLLAMA_KEEP_ALIVE=30m` | ~1.5–3 s desde la primera consulta |
| Primer `/approve` tras arrancar: > 5 s (una vez) | Primera escritura en Neo4j (réplica del reclamo) | **Pendiente**: hacer una escritura de calentamiento o mover la réplica a una cola asíncrona (outbox) | los siguientes: 100–170 ms |

## 3. Costo por turno si los agentes usaran un LLM cloud

Tokens de **entrada** medidos construyendo los prompts reales de cada nodo para los 31 casos
(tokenizer de Qwen2.5; otros tokenizers difieren ~±20%). Tokens de **salida**: supuesto.

| Nodo | Entrada media (medida) | Salida (supuesto) |
|---|---|---|
| Router (prompt de clasificación + mensaje) | 175 | 25 |
| `query_agent` (2 vueltas ReAct + esquemas de tools + resultado SQL) | 582 | 60 |
| `knowledge_agent` (prompt + 4 documentos del grafo con vecinos) | 480 | 120 |
| `complaint_agent` (prompt + esquema del borrador) | 355 | 80 |
| **Turno promedio** (mezcla de los casos de Evals) | **464** | **82** |

Con un modelo cloud económico de referencia (gpt-4o-mini: US$ 0.15 / 0.60 por 1M tokens de
entrada / salida; **verificar precio vigente**):

| Escenario | Costo por turno | Por 100 000 turnos |
|---|---|---|
| Todos los nodos en el LLM cloud | US$ 0.000119 | **US$ 11.9** |
| Router LoRA local + resto en el LLM cloud | US$ 0.000084 | **US$ 8.4** (−29%) |
| Todo local (LoRA + Ollama) | 0 marginal | costo de infraestructura (GPU on-premise) |

**Conclusión honesta**: el router consume ~32% de los tokens de entrada, así que llevarlo a local
ahorra ~29% del costo variable. Pero con modelos cloud baratos el costo absoluto ya es bajo. **El
argumento fuerte para el modelo local no es el costo**, sino:
1. **Soberanía del dato**: el mensaje del cliente no sale del banco (requisito habitual en banca y
   coherente con una arquitectura on-premise como SQL Server y pgvector).
2. **Precisión**: 100% del LoRA frente a 36.7% del mismo modelo sin ajustar.
3. **Sin dependencia externa** para el paso que se ejecuta en *cada* turno (sin cuotas, cortes ni
   cambios de versión del proveedor).

## 4. Decisiones de diseño que bajan costo y latencia

| Decisión | Efecto medido |
|---|---|
| Guardrail determinista **antes** del router | injection y acceso ajeno (5 de 31 casos) se resuelven en < 1 ms y 0 tokens, sin tocar ningún modelo |
| Respuestas por plantilla para bloqueos y aclaraciones | fuera de alcance y ambiguos (5 casos más) no consumen tokens después del router |
| SQL parametrizado en lugar de text-to-SQL | 2 ms por consulta; sin tokens para generar SQL ni riesgo de queries arbitrarias |
| Abstención si la similitud < 0.65 | no se llama al LLM para preguntas que la base no cubre |
| Contexto acotado: top-4 documentos, historial de 6 mensajes, `max_tokens` 512 | ~480 tokens por consulta de conocimiento |
| Tope de 4 vueltas en el loop ReAct | costo máximo por consulta acotado |
| LoRA fusionado con el modelo base al cargar (`merge_and_unload`) | misma latencia que el modelo base, sin overhead del adaptador |
| Streaming SSE | no reduce la latencia total, pero el primer evento llega en ~200 ms y el usuario ve el avance por nodo |

## 5. Costo de entrenamiento

| | |
|---|---|
| Fine-tuning LoRA (427 ejemplos, 3 épocas) | **5.6 min** en RTX 3050 · pico 1.9 GB VRAM |
| Reentrenamiento v2 tras los Evals | 5.1 min |
| Adaptador resultante | 35 MB (versionable y desplegable aparte del modelo base) |

Iterar sobre el router (detectar una falla en Evals → agregar datos → reentrenar → re-evaluar)
toma menos de 15 minutos de punta a punta en hardware de consumo.

## 6. Qué optimizaría a continuación

1. **Latencia del router LoRA** (95% del turno):
   - generar solo las claves no nulas (de ~21 a ~8 tokens);
   - servir con llama.cpp/ONNX o vLLM en lugar de `transformers.generate`;
   - o reemplazar la generación por una cabeza de clasificación sobre el encoder (un solo forward,
     ~20–50 ms estimado) y dejar la generación solo para las entidades.
2. **Cascada de routers**: reglas de alta precisión primero (son instantáneas) y LoRA solo cuando
   las reglas no están seguras. Con la confianza calibrada se ahorraría el costo del LoRA en la
   mayoría de los turnos simples.
3. **Caché semántica** de respuestas de conocimiento: las preguntas de políticas se repiten mucho y
   su respuesta no depende del cliente.
4. **Réplica a Neo4j asíncrona** (outbox): saca la escritura del camino crítico del `/approve`.
