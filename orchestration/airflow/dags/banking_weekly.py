"""DAG semanal del asistente bancario (Airflow 3).

    reindex_knowledge ─► evals_gate ─► complaints_report ─┐
                                                          ├─► run_summary (siempre)
    (si los Evals no cumplen los umbrales, el informe NO se genera)

- reindex_knowledge: recarga la base de conocimiento en pgvector (idempotente: upsert por id).
- evals_gate: Evals de punta a punta contra la base RECIÉN indexada; exit 1 si alguna métrica baja
  del umbral → las tareas siguientes quedan en upstream_failed. Una política mal cargada no llega
  a producción ni al informe.
- complaints_report: crew de CrewAI con Ollama on-premise (informe ejecutivo de reclamos).
- run_summary: resume la corrida (métricas de los Evals + verificaciones del informe), corra o no
  el informe, y lo deja en el volumen de salida.

Cada tarea usa su propio entorno de Python (app y crew tienen dependencias incompatibles): el
equivalente a ExternalPythonOperator, con BashOperator para que el comando sea explícito.
"""

import json
import os
from pathlib import Path

import pendulum
from airflow.sdk import Param, dag, task

PROJECT = os.environ.get("BANK_PROJECT_DIR", "/opt/project")
OUTPUT = os.environ.get("BANK_OUTPUT_DIR", "/opt/airflow/output")
# Carpeta de salida por corrida. En Airflow 3 una corrida manual no tiene logical_date ({{ ds }} no
# existe): run_after está siempre
RUN_DATE = "{{ dag_run.run_after | ds }}"
APP_PY = os.environ.get("BANK_APP_PYTHON", "/opt/airflow/venvs/app/bin/python")
CREW_PY = os.environ.get("BANK_CREW_PYTHON", "/opt/airflow/venvs/crew/bin/python")


@dag(
    dag_id="banking_assistant_weekly",
    schedule="0 7 * * 1",  # lunes 07:00
    start_date=pendulum.datetime(2026, 10, 1, tz="America/La_Paz"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=2)},
    params={
        "complaints_source": Param("synthetic", enum=["synthetic", "sqlite"], description="Origen de los reclamos"),
    },
    tags=["banca", "rag", "evals", "crewai"],
    doc_md=__doc__,
)
def banking_assistant_weekly():
    @task.bash(cwd=PROJECT)
    def reindex_knowledge() -> str:
        return f"{APP_PY} backend/scripts/seed_pgvector.py"

    @task.bash(cwd=PROJECT, retries=0)  # un umbral no cumplido no se arregla reintentando
    def evals_gate() -> str:
        return (
            f"{APP_PY} evals/run_evals.py --knowledge pgvector --check --quiet --reports-dir {OUTPUT}/{RUN_DATE}/evals"
        )

    @task.bash(cwd=PROJECT, execution_timeout=pendulum.duration(minutes=15))
    def complaints_report() -> str:
        return (
            f"{CREW_PY} crew/complaints_crew.py --source {{{{ params.complaints_source }}}} "
            f"--base-url $OLLAMA_BASE_URL --out {OUTPUT}/{RUN_DATE}/complaints_report.md"
        )

    @task(trigger_rule="all_done")
    def run_summary(dag_run=None) -> dict:
        ds = dag_run.run_after.strftime("%Y-%m-%d")
        folder = Path(OUTPUT) / ds
        evals_file = folder / "evals" / "latest.json"
        report_file = folder / "complaints_report.md"
        summary: dict = {"fecha": ds, "evals": None, "informe": None}
        if evals_file.exists():
            data = json.loads(evals_file.read_text(encoding="utf-8"))
            m = data["metrics"]
            summary["evals"] = {
                "casos_ok": f"{m['passed']}/{m['cases']}",
                "citas": m["citation_hit_rate"],
                "umbrales_ok": not data["violations"],
                "violaciones": data["violations"],
            }
        if report_file.exists():
            text = report_file.read_text(encoding="utf-8")
            checks = text.split("## Verificación automática", 1)[-1]
            summary["informe"] = {
                "archivo": str(report_file),
                "advertencias": checks.count("⚠️"),
            }
        lines = [f"# Corrida semanal {ds}", ""]
        if summary["evals"]:
            e = summary["evals"]
            lines.append(
                f"- Evals: {e['casos_ok']} · citas {e['citas']} · umbrales {'OK' if e['umbrales_ok'] else 'NO cumplidos'}"
            )
            lines += [f"  - {v}" for v in e["violaciones"]]
        else:
            lines.append("- Evals: no se ejecutaron (falló la reindexación)")
        if summary["informe"]:
            lines.append(f"- Informe de reclamos: {summary['informe']['advertencias']} advertencias para el revisor")
        else:
            lines.append("- Informe de reclamos: NO generado (la compuerta de Evals no pasó o el crew falló)")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary  # queda en XCom, visible en la UI

    report = complaints_report()
    reindex_knowledge() >> evals_gate() >> report >> run_summary()


banking_assistant_weekly()
