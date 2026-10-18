# Fine-tuning LoRA — router de intenciones

Qwen2.5-0.5B-Instruct ajustado con LoRA para una tarea acotada: **clasificar la intención del cliente
y extraer entidades en JSON estricto**. Reemplaza al router en el grafo (`ROUTER_BACKEND=lora`).

```json
{"intent":"queja_disputa","producto":"tarjeta_debito","monto":350.0,"moneda":"BOB"}
```

**Por qué esta tarea**: el router se ejecuta en *cada* turno. Es donde un modelo pequeño, local y
especializado da el mayor ahorro frente a un LLM grande, y donde la salida es medible sin ambigüedad.

## Resultados (split de test: plantillas nunca vistas en entrenamiento)

v1 y v2: 5 intenciones, 120 ejemplos de test.

| Backend | Accuracy | Macro-F1 | JSON válido | Producto EM | Monto EM | Moneda EM | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| Reglas (palabras clave) | 0.592 | 0.614 | 1.00 | 0.79 | 1.00 | 1.00 | 0.2 | 0.2 |
| Qwen2.5-0.5B base (zero-shot) | 0.367 | 0.286 | 0.91 | 0.37 | 0.93 | 0.24 | 967 | 1254 |
| Qwen2.5-0.5B + LoRA v1 | 0.958 | 0.958 | 1.00 | 0.86 | 1.00 | 1.00 | 747 | 896 |
| **Qwen2.5-0.5B + LoRA v2** | **1.000** | **1.000** | **1.00** | **0.88** | **1.00** | **1.00** | 773 | 912 |

Recall por intención (v1/v2):

| Backend | consulta_cuenta | conocimiento_producto | queja_disputa | fuera_de_alcance | ambiguo |
|---|---|---|---|---|---|
| Reglas | 0.62 | 0.54 | **0.25** | 0.54 | 1.00 |
| Base | 1.00 | 0.00 | 0.79 | 0.00 | 0.04 |
| LoRA v1 | 0.79 | 1.00 | 1.00 | 1.00 | 1.00 |
| **LoRA v2** | **1.00** | 1.00 | **1.00** | 1.00 | 1.00 |

### v3: intención nueva `accion_tarjeta` (bloqueo de tarjeta con confirmación del cliente)

El test v3 son los **mismos 120 ejemplos** más 23 de la intención nueva (143). El generador agrega
filas al test conservado solo para intenciones que no tenía, así la comparación con v2 sigue siendo válida.

| Backend | Accuracy (143) | Macro-F1 | Producto EM | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|
| Reglas | 0.622 | 0.646 | 0.76 | 0.2 | 0.3 |
| LoRA v2 (no conoce la intención) | 0.811 | 0.746 | 0.87 | 743 | 923 |
| **LoRA v3** | **0.965** | **0.964** | **0.93** | 721 | 874 |

| Backend | consulta_cuenta | conocimiento_producto | queja_disputa | accion_tarjeta | fuera_de_alcance | ambiguo |
|---|---|---|---|---|---|---|
| Reglas | 0.62 | 0.54 | 0.25 | 0.78 | 0.54 | 1.00 |
| LoRA v2 | 1.00 | 1.00 | 1.00 | 0.00 | 0.83 ¹ | 1.00 |
| **LoRA v3** | **1.00** | 0.96 | **1.00** | **0.83** | **1.00** | **1.00** |

¹ v2 se entrenó con el prompt de 5 intenciones; con el prompt nuevo, parte de los pedidos de bloqueo
caen en fuera de alcance.

- **Primer intento de v3 (n=700): rechazado.** Con 6 intenciones quedaban ~93 ejemplos por
  intención y retrocedía donde v2 era perfecto: consultas 1.00 → 0.88 ("¿cuánta plata me queda?",
  otra vez), conocimiento 1.00 → 0.83 ("¿cobran mantenimiento…?" como queja). Con n=900 (~120 por
  intención, como v2) se recupera: sobre los 120 ejemplos originales v3 acierta 119.
- Plantillas de **contraste** en entrenamiento: "¿cómo bloqueo mi tarjeta si me la roban?"
  (conocimiento) y "me bloquearon la tarjeta y quiero reclamar" (queja) frente a "bloquea mi tarjeta" (acción).
- Un error del router en esta intención no ejecuta nada: el bloqueo exige la confirmación explícita
  del cliente.
- En los Evals de punta a punta, **LoRA v3 + Neo4j: 39/39**.

Lectura (v1/v2):
- El modelo base **sin** fine-tuning es peor que las reglas (y 9% de sus salidas no son JSON válido):
  el valor está en el ajuste, no en "usar un LLM".
- Las reglas pierden el **75% de las quejas** expresadas con otras palabras ("me clavaron un cobro").
  En banca es el error más caro: un reclamo no detectado no pasa por HITL. LoRA: recall 1.00.
- **v1 → v2**: los 5 errores de v1 venían de una sola plantilla de test, "¿cuánta plata me queda?".
  "plata" solo aparecía en plantillas de quejas. Los Evals de la Fase 6 lo detectaron. v2 agrega
  8 plantillas coloquiales **solo en entrenamiento** ("¿cuánta plata tengo en mi cuenta?",
  "platita"...), con el test intacto (mismo archivo). Esto corrige la cobertura de vocabulario, no
  memoriza el test. Aun así, 100% en un test sintético de 24 plantillas no implica 100% en producción.
- En v2, la confianza (probabilidad de los tokens de `intent`) salía ~0.99 casi siempre: con 100%
  de aciertos no había errores contra los cuales medirla. En v3 sí los hay, y la confianza resultó
  estar bien calibrada (sección siguiente).

## Calibración de la confianza y cascada reglas → LoRA (`calibrate.py`)

**Hipótesis inicial (descartada)**: el LoRA está sobreconfiado y hay que calibrarlo con temperatura.
Se midió sobre el test (143 ejemplos, plantillas nunca vistas) comparando la confianza de producción
(greedy) con una distribución sobre las 6 etiquetas, puntuadas una por una y escaladas con una
temperatura ajustada por validación cruzada agrupada por plantilla:

| Confianza | ECE | Brier | AUROC aciertos vs errores | Costo extra |
|---|---|---|---|---|
| **Greedy (producción)** | 0.024 | **0.0226** | **0.971** | 0 |
| 6 etiquetas + temperatura (fuera de muestra) | **0.008** | 0.0255 | 0.961 | +172 ms por turno |

La confianza greedy **ya está calibrada**: cuando dice ≥ 0.9 acierta 125 de 125, y por debajo de
0.7 acierta la mitad. La temperatura mejora el ECE en un margen que con 5 errores es ruido, empeora
la separación de errores y cuesta 172 ms. **No se usa**.

**Lo que sí funcionó**:

1. **Un umbral de aclaración propio del LoRA** (`LORA_MIN_CONFIDENCE=0.7`): las reglas y el LoRA
   miden la confianza en escalas distintas, y compartían el umbral de 0.6.
2. **Cascada** (`ROUTER_BACKEND=cascade`): las reglas resuelven solas las intenciones en que no se
   equivocan, y el resto va al LoRA. Las intenciones se eligen en train+val (precisión ≥ 0.99 con
   soporte ≥ 30) y se miden en test:

| Intención (reglas, conf. ≥ 0.6) | Precisión train+val | Precisión test | Quién la resuelve |
|---|---|---|---|
| consulta_cuenta | 1.000 (66) | 1.000 (15) | reglas |
| accion_tarjeta | 1.000 (72) | 1.000 (18) | reglas |
| fuera_de_alcance | 1.000 (79) | 1.000 (13) | reglas |
| queja_disputa | 0.968 (63) | 1.000 (6) | LoRA: "¿en cuánto tiempo responden un reclamo?" no es un reclamo |
| conocimiento_producto | 0.611 (157) | 0.765 (17) | LoRA: "tarjeta" y "bloqueo" no alcanzan |

| Router (test, 143) | Accuracy | Errores respondidos | Aclaraciones | Latencia media del router | Turnos sin modelo |
|---|---|---|---|---|---|
| LoRA, umbral 0.6 (antes) | 0.965 | 3 | 4 | 867 ms | 0% |
| LoRA, umbral 0.7 | 0.965 | 1 | 8 | 867 ms | 0% |
| **Cascada, umbral 0.7** | **0.986** | **0** | **3** | **599 ms** | **32%** |

- La cascada **también sube la accuracy**: 3 de los 5 errores del LoRA eran pedidos de bloqueo
  ("me robaron la billetera, bloquea mi plástico" → queja) que las reglas reconocen por el verbo.
- Los reclamos los clasifica siempre el LoRA: un reclamo no detectado no pasa por HITL.
- En los Evals de punta a punta, cascada + Neo4j: **39/39**, 35% de los turnos sin modelo y
  latencia media del router 761 → 529 ms.
- Límites: el umbral 0.7 se eligió mirando el test (val no sirve: el modelo acierta todo ahí) y
  la precisión de las reglas se midió sobre frases sintéticas. En producción, las dos cosas se
  vuelven a medir con conversaciones reales etiquetadas; `calibrate.py analyze` lo recalcula en
  segundos a partir de `reports/router_scores.jsonl`.

### Entrenamiento

| | |
|---|---|
| Método | LoRA bf16, r=16, α=32, dropout 0.05, todas las proyecciones (q,k,v,o,gate,up,down) |
| Parámetros entrenables | 8.8M de 503M (**1.75%**) |
| Datos | v2: 427 train / 53 val / 120 test (5 intenciones) · v3: 640 / 80 / 143 (6 intenciones; test fijo + filas de la intención nueva) |
| Hiperparámetros | 3 épocas, lr 2e-4 coseno, batch efectivo 16 (2 × 8 acumulación), gradient checkpointing |
| Loss | v1: train 0.063 · val 0.026 — v2: train 0.063 · val 0.008 — v3: train 0.056 · val 0.007 (solo sobre los tokens del JSON) |
| Hardware / tiempo | RTX 3050 Laptop 4 GB · **5.6 min** · pico 1.9 GB VRAM |
| Adaptador | 35 MB (`adapters/`, no se versiona: se regenera en ~6 min) |

### Latencia y costo del router

| Backend | Latencia por turno | Costo marginal | Dónde corre |
|---|---|---|---|
| Reglas | < 1 ms | 0 | CPU |
| LoRA en GPU (RTX 3050) | ~730 ms (p95 ~950 ms) | 0 | on-premise |
| LoRA en CPU | ~3.6 s | 0 | on-premise |

~21 tokens de salida y ~130 de entrada por clasificación. El adaptador se fusiona con los pesos
base al cargar (`merge_and_unload`), así que no agrega latencia frente al modelo base.

## Dataset sintético (`generate_dataset.py`)

- Plantillas por intención con slots coherentes (`{p_card}`, `{p_account}`, `{p_credit}`...) y
  montos en formatos bolivianos ("Bs 1.250,50", "350 bolivianos", "$us 64").
- Ruido de chat real: sin tildes, minúsculas, MAYÚSCULAS, typos por transposición, saludos al inicio.
- **Split por plantilla**: ~20% de las plantillas de cada intención solo aparecen en test.

## Cómo ejecutar

```powershell
# 1. Dependencias (ORDEN IMPORTANTE: torch con CUDA primero)
pip install torch --index-url https://download.pytorch.org/whl/cu126
pip install -r finetune/requirements.txt
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"   # → +cu126 True

# 2. Dataset, entrenamiento, evaluación
python finetune/generate_dataset.py --n 600 --seed 42
python finetune/train_lora.py                  # o --qlora para 4 bits
python finetune/eval_classifier.py             # escribe finetune/reports/router_eval.{json,md}
python finetune/calibrate.py collect           # puntuaciones de reglas y LoRA sobre val y test (~4 min)
python finetune/calibrate.py analyze           # calibración y cascada → reports/calibration.{json,md}

# 3. Probar
python finetune/infer.py "me cobraron Bs 350 dos veces en la tarjeta"
python finetune/infer.py --no-adapter "..."    # mismo texto con el modelo base, para comparar

# 4. Usarlo en el backend
$env:ROUTER_BACKEND="cascade"; uvicorn app.main:app --port 8000   # desde backend/ (o "lora": solo el modelo)
```

## Integración en el grafo

`backend/app/llm/lora_router.py` (carga diferida: torch solo se importa con `ROUTER_BACKEND=lora`).
El formato de prompt/salida vive en `backend/app/llm/intent_format.py` y lo comparten el
entrenamiento y la inferencia. En el nodo `router`:
- los montos extraídos por regex prevalecen sobre los del modelo;
- si el modelo falla (no entrenado, JSON inválido, sin memoria), cae al router por reglas y la
  respuesta lleva `degraded: true`.

## Qué haría distinto con más tiempo

- Ampliar vocabulario coloquial con frases reales (anonimizadas) del canal, no solo plantillas
  (v2 lo hizo a mano para "plata"; en producción saldría del monitoreo de conversaciones).
- Bajar latencia: limitar la salida a las claves no nulas, exportar a ONNX/llama.cpp, o usar un
  clasificador de cabeza lineal sobre el encoder para la intención y dejar el LLM solo para entidades.
- Comparar con Llama-3.2-3B + QLoRA (el script ya lo soporta con `--qlora --base-model ...`).
