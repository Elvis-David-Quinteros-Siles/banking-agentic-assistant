# Informe ejecutivo de reclamos con CrewAI

Proceso **batch** (fuera del camino del cliente) que genera el informe semanal de reclamos para la
gerencia: tres agentes de CrewAI con un LLM **on-premise** (qwen2.5:3b en Ollama), sobre los reclamos
que el sistema registró (`--source sqlite`) o sobre 60 reclamos ficticios con un patrón plantado.

```
report_data.py (código)          CrewAI (proceso secuencial)                      report_data.py (código)
estadísticas por categoría  →  1. Analista de calidad: patrones y causas    →  cifras de cada hallazgo
tendencia semanal               2. Especialista en normativa: política        insertadas por categoría
señales (crecimiento, alta)        aplicable (tool consultar_politica)         + verificaciones
mapeo categoría → políticas     3. Redactor: hallazgos ESTRUCTURADOS           = borrador para revisión
                                   (output_pydantic, sin cifras)                 humana
```

Ejemplo de salida: [reports/complaints_report.md](reports/complaints_report.md).

## Por qué un crew acá y LangGraph en el chat

| | Chat (LangGraph) | Informe (CrewAI) |
|---|---|---|
| Latencia | por turno, < 1 s sin LLM | batch, ~1 min está bien |
| Control | grafo explícito, pausas `interrupt()`, estado por conversación | roles y tareas secuenciales |
| Qué aporta el LLM | entender la consulta | interpretar patrones y redactar |
| Salida | respuesta a un cliente | borrador para un comité, con revisión humana |

## El mismo principio que el resto del sistema: el código pone las cifras

Cada corrección salió de correr el crew y leer lo que producía (como los Evals del chat):

| # | Qué hizo el modelo | Corrección | Cómo quedó |
|---|---|---|---|
| 1 | Una respuesta de **11.800 tokens** (generación sin fin) | `max_tokens=900`, `max_iter=4`, `max_execution_time=180` | Corridas de 31–110 s |
| 2 | Inventó un agregado ("78% del total", suma de tres categorías) | Verificación: toda cifra del texto debe existir en los datos | Detectado |
| 3 | Asignó políticas equivocadas (transferencias → POL-003) | El código entrega la tabla categoría → políticas candidatas | Desde ahí cita POL-011, POL-012, POL-004 |
| 4 | No vio el crecimiento plantado (0 → 2 → 2 → 10) | El código calcula las **señales**; el modelo las explica | La señal siempre está en la cabecera |
| 5 | Dijo que las transferencias **disminuyeron** (crecieron) usando solo cifras correctas | Chequeo de contradicción: categoría en crecimiento + verbo de baja en la misma oración | Detectado |
| 6 | Asoció cifras reales a la categoría equivocada ("fraude: de 2 a 10"; el 2 y el 10 son de otra categoría) | **Salida estructurada** (`output_pydantic`): el modelo describe con palabras y el código inserta las cifras de cada categoría | Imposible por construcción |
| 7 | Afirmó crecimientos que no existen ("cobro indebido creció con fuerza": 5 → 4 → 6 → 8) | Chequeo en hallazgos y en el resumen, oración por oración | Detectado |

La #2, la #5 y la #6 muestran que **verificar cifras es necesario pero no suficiente**: un texto
puede usar solo números correctos y afirmar lo contrario de lo que muestran.

## Resultados (versión final, 5 corridas, qwen2.5:3b en una GPU de 4 GB)

| | Resultado |
|---|---|
| Salida estructurada válida | **5/5** |
| Cifras junto a cada hallazgo correctas | **5/5** (las pone el código) |
| Corridas sin ninguna advertencia | 1/5 |
| El modelo escribió cifras propias en el texto (se marcan) | 3/5 |
| Políticas intercambiadas entre categorías (se marcan) | 1/5 |
| Tiempo · tokens por informe | 42–90 s · 10–20 mil |

Con un modelo de 3B el informe es un **borrador para revisión humana**, igual que los reclamos pasan
por HITL: las verificaciones le dicen al revisor exactamente qué corregir. Un modelo más grande
reduciría las advertencias; las garantías (cifras por categoría, señales, mapeo de políticas) no
dependen del modelo.

## Uso

CrewAI va en un entorno aparte: fija versiones de `pydantic`, `openai` y `mcp` distintas a las del backend.

```bash
python -m venv .venv-crew
.venv-crew/Scripts/pip install -r crew/requirements.txt
docker compose up -d ollama                                   # qwen2.5:3b
.venv-crew/Scripts/python crew/complaints_crew.py             # datos sintéticos
.venv-crew/Scripts/python crew/complaints_crew.py --source sqlite   # reclamos registrados
```

`crew/tests/` prueba las partes deterministas (estadísticas, señales, normalización, verificaciones
con los casos reales de la tabla) sin CrewAI ni LLM; corre en CI.
