# Fine-tuning LoRA — router de intenciones

Qwen2.5-0.5B-Instruct ajustado con LoRA para una tarea acotada: **clasificar la intención del cliente
y extraer entidades en JSON estricto**. Reemplaza al router en el grafo (`ROUTER_BACKEND=lora`).

```json
{"intent":"queja_disputa","producto":"tarjeta_debito","monto":350.0,"moneda":"BOB"}
```

**Por qué esta tarea**: el router se ejecuta en *cada* turno. Es donde un modelo pequeño, local y
especializado da el mayor ahorro frente a un LLM grande, y donde la salida es medible sin ambigüedad.

## Resultados (split de test: 120 ejemplos de plantillas nunca vistas en entrenamiento)

| Backend | Accuracy | Macro-F1 | JSON válido | Producto EM | Monto EM | Moneda EM | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| Reglas (palabras clave) | 0.592 | 0.614 | 1.00 | 0.79 | 1.00 | 1.00 | 0.3 | 0.4 |
| Qwen2.5-0.5B base (zero-shot) | 0.367 | 0.286 | 0.91 | 0.37 | 0.93 | 0.24 | 993 | 1328 |
| **Qwen2.5-0.5B + LoRA** | **0.958** | **0.958** | **1.00** | **0.86** | **1.00** | **1.00** | 773 | 954 |

Recall por intención:

| Backend | consulta_cuenta | conocimiento_producto | queja_disputa | fuera_de_alcance | ambiguo |
|---|---|---|---|---|---|
| Reglas | 0.62 | 0.54 | **0.25** | 0.54 | 1.00 |
| Base | 1.00 | 0.00 | 0.79 | 0.00 | 0.04 |
| **LoRA** | 0.79 | 1.00 | **1.00** | 1.00 | 1.00 |

Lectura:
- El modelo base **sin** fine-tuning es peor que las reglas (y 9% de sus salidas no son JSON válido):
  el valor está en el ajuste, no en "usar un LLM".
- Las reglas pierden el **75% de las quejas** expresadas con otras palabras ("me clavaron un cobro").
  En banca es el error más caro: un reclamo no detectado no pasa por HITL. LoRA: recall 1.00.
- Los 5 errores de LoRA vienen de **una sola plantilla** de test: "¿cuánta plata me queda?". El
  coloquialismo "plata" no aparece en ninguna plantilla de consulta de entrenamiento. Es una brecha
  de datos, no se corrigió reentrenando sobre el test (eso invalidaría la medición).
- La confianza (probabilidad de los tokens de `intent`) sale ~0.99 casi siempre: el modelo está
  **sobreconfiado**. El umbral de aclaración del router sirve poco con LoRA hasta calibrarlo (Fase 6).

### Entrenamiento

| | |
|---|---|
| Método | LoRA bf16, r=16, α=32, dropout 0.05, todas las proyecciones (q,k,v,o,gate,up,down) |
| Parámetros entrenables | 8.8M de 503M (**1.75%**) |
| Datos | 427 train / 53 val / 120 test (sintéticos, balanceados en 5 intenciones) |
| Hiperparámetros | 3 épocas, lr 2e-4 coseno, batch efectivo 16 (2 × 8 acumulación), gradient checkpointing |
| Loss | train 0.063 · val 0.026 (solo sobre los tokens del JSON de respuesta) |
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

# 3. Probar
python finetune/infer.py "me cobraron Bs 350 dos veces en la tarjeta"
python finetune/infer.py --no-adapter "..."    # mismo texto con el modelo base, para comparar

# 4. Usarlo en el backend
$env:ROUTER_BACKEND="lora"; uvicorn app.main:app --port 8000   # desde backend/
```

## Integración en el grafo

`backend/app/llm/lora_router.py` (carga diferida: torch solo se importa con `ROUTER_BACKEND=lora`).
El formato de prompt/salida vive en `backend/app/llm/intent_format.py` y lo comparten el
entrenamiento y la inferencia. En el nodo `router`:
- los montos extraídos por regex prevalecen sobre los del modelo;
- si el modelo falla (no entrenado, JSON inválido, sin memoria), cae al router por reglas y la
  respuesta lleva `degraded: true`.

## Qué haría distinto con más tiempo

- Ampliar vocabulario coloquial con frases reales (anonimizadas) del canal, no solo plantillas.
- Calibrar la confianza (temperature scaling sobre val) para que el umbral de aclaración funcione.
- Bajar latencia: limitar la salida a las claves no nulas, exportar a ONNX/llama.cpp, o usar un
  clasificador de cabeza lineal sobre el encoder para la intención y dejar el LLM solo para entidades.
- Comparar con Llama-3.2-3B + QLoRA (el script ya lo soporta con `--qlora --base-model ...`).
