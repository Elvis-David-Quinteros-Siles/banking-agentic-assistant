Test: 143 ejemplos (plantillas nunca vistas). Temperatura por CV agrupada: [0.8, 0.9] → final 0.8 (en val: 1.0).

| Confianza | Accuracy | Confianza media | ECE | Brier | AUROC aciertos vs errores |
|---|---|---|---|---|---|
| Greedy (la de producción) | 0.965 | 0.953 | 0.024 | 0.0226 | 0.971 |
| 6 etiquetas, T=1 | 0.965 | 0.961 | 0.020 | 0.0252 | 0.959 |
| 6 etiquetas, T por CV (fuera de muestra) | 0.965 | 0.967 | 0.008 | 0.0255 | 0.961 |

Diagrama de fiabilidad (greedy):

| Rango | n | Accuracy | Confianza media |
|---|---|---|---|
| 0.4-0.5 | 2 | 0.50 | 0.47 |
| 0.5-0.6 | 2 | 0.50 | 0.56 |
| 0.6-0.7 | 4 | 0.50 | 0.66 |
| 0.7-0.8 | 4 | 1.00 | 0.77 |
| 0.8-0.9 | 6 | 0.83 | 0.84 |
| 0.9-1.0 | 125 | 1.00 | 0.99 |

Precisión de las reglas por intención (train+val, confianza >= 0.6):

| Intención | Soporte | Precisión | ¿La resuelven las reglas? |
|---|---|---|---|
| accion_tarjeta | 72 | 1.000 | sí |
| conocimiento_producto | 157 | 0.611 | no → LoRA |
| consulta_cuenta | 66 | 1.000 | sí |
| fuera_de_alcance | 79 | 1.000 | sí |
| queja_disputa | 63 | 0.968 | no → LoRA |

Umbral de aclaración del LoRA (test):

| Umbral | LoRA solo: errores respondidos / aclaraciones | Cascada: errores respondidos / aclaraciones |
|---|---|---|
| 0.5 | 4 / 2 | 2 / 1 |
| 0.6 | 3 / 4 | 1 / 2 |
| 0.7 | 1 / 8 | 0 / 3 |
| 0.8 | 1 / 12 | 0 / 3 |
| 0.9 | 0 / 18 | 0 / 7 |
| 0.95 | 0 / 21 | 0 / 10 |

Cascada: 32% de los turnos por reglas · accuracy 0.965 → 0.986 · latencia media del router 867.1 → 598.6 ms.
