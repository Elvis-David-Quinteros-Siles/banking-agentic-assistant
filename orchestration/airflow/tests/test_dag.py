"""Integridad del DAG: importa sin errores y la compuerta de Evals está donde debe."""

from pathlib import Path

from airflow.models.dagbag import DagBag

DAGS = Path(__file__).resolve().parents[1] / "dags"


def test_dag_imports_and_gate_order():
    bag = DagBag(dag_folder=str(DAGS), include_examples=False)
    assert bag.import_errors == {}
    dag = bag.dags.get("banking_assistant_weekly")  # get_dag() consulta la base de metadatos; CI no la tiene
    assert dag is not None
    tasks = {t.task_id: t for t in dag.tasks}
    assert set(tasks) == {"reindex_knowledge", "evals_gate", "complaints_report", "run_summary"}
    # El informe depende de la compuerta, y la compuerta de la reindexación
    assert tasks["evals_gate"].upstream_task_ids == {"reindex_knowledge"}
    assert tasks["complaints_report"].upstream_task_ids == {"evals_gate"}
    # Un umbral no cumplido no se reintenta; el resumen corre aunque algo falle
    assert tasks["evals_gate"].retries == 0
    assert tasks["run_summary"].trigger_rule == "all_done"
    assert dag.catchup is False and dag.max_active_runs == 1
