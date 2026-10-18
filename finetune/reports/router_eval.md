| Backend | Accuracy | Macro-F1 | JSON válido | Producto EM | Monto EM | Moneda EM | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| rules | 0.622 | 0.646 | 1.00 | 0.76 | 1.00 | 1.00 | 0.2 | 0.3 |
| lora_v2 | 0.811 | 0.746 | 1.00 | 0.87 | 1.00 | 1.00 | 743.3 | 922.8 |
| lora_v3 | 0.965 | 0.964 | 1.00 | 0.93 | 1.00 | 1.00 | 721.0 | 873.7 |

Recall por intención:

| Backend | consulta_cuenta | conocimiento_producto | queja_disputa | accion_tarjeta | fuera_de_alcance | ambiguo |
|---|---|---|---|---|---|---|
| rules | 0.62 | 0.54 | 0.25 | 0.78 | 0.54 | 1.00 |
| lora_v2 | 1.00 | 1.00 | 1.00 | 0.00 | 0.83 | 1.00 |
| lora_v3 | 1.00 | 0.96 | 1.00 | 0.83 | 1.00 | 1.00 |
