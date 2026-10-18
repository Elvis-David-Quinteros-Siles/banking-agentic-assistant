# Airflow 3 con dos entornos de Python aislados para las tareas:
#   venvs/app  → backend (LangGraph, pgvector, Evals)
#   venvs/crew → CrewAI (fija versiones de pydantic/openai/mcp incompatibles con el backend)
# El código del proyecto NO se copia: se monta en /opt/project (solo lectura).
FROM apache/airflow:3.1.3-python3.12

COPY --chown=airflow:root backend/requirements.txt /tmp/app-requirements.txt
COPY --chown=airflow:root crew/requirements.txt /tmp/crew-requirements.txt

RUN python -m venv /opt/airflow/venvs/app \
 && /opt/airflow/venvs/app/bin/pip install --no-cache-dir -r /tmp/app-requirements.txt \
 && python -m venv /opt/airflow/venvs/crew \
 && /opt/airflow/venvs/crew/bin/pip install --no-cache-dir -r /tmp/crew-requirements.txt

# Carpetas de los volúmenes con dueño airflow (un volumen nuevo hereda la propiedad de la imagen)
RUN mkdir -p /opt/airflow/hf-cache /opt/airflow/output
