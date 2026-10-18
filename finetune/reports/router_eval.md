| Backend | Accuracy | Macro-F1 | JSON válido | Producto EM | Monto EM | Moneda EM | p50 (ms) | p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| rules | 0.592 | 0.614 | 1.00 | 0.79 | 1.00 | 1.00 | 0.2 | 0.2 |
| base | 0.367 | 0.286 | 0.91 | 0.37 | 0.93 | 0.24 | 967.0 | 1254.2 |
| lora_v1 | 0.958 | 0.958 | 1.00 | 0.86 | 1.00 | 1.00 | 746.6 | 896.2 |
| lora_v2 | 1.000 | 1.000 | 1.00 | 0.88 | 1.00 | 1.00 | 773.0 | 911.9 |

Recall por intención:

| Backend | consulta_cuenta | conocimiento_producto | queja_disputa | fuera_de_alcance | ambiguo |
|---|---|---|---|---|---|
| rules | 0.62 | 0.54 | 0.25 | 0.54 | 1.00 |
| base | 1.00 | 0.00 | 0.79 | 0.00 | 0.04 |
| lora_v1 | 0.79 | 1.00 | 1.00 | 1.00 | 1.00 |
| lora_v2 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
