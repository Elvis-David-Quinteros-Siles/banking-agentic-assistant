# Imagen de la demo pública: frontend + API en un solo contenedor y un solo puerto.
#
# Modo liviano (router por reglas, BM25 en memoria, respuestas por plantilla): no necesita GPU,
# Neo4j ni LLM, y usa solo datos ficticios. Pensada para Hugging Face Spaces (puerto 7860) o Render.
#
#   docker build -t banking-agent-demo .
#   docker run -p 7860:7860 banking-agent-demo      → http://localhost:7860

# --- 1. Build del frontend ----------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

# --- 2. Backend que sirve la API y el frontend compilado ---------------------
FROM python:3.12-slim
# Vincula la imagen publicada en GHCR con el repositorio (hereda su visibilidad pública)
LABEL org.opencontainers.image.source="https://github.com/Elvis-David-Quinteros-Siles/banking-agentic-assistant" \
      org.opencontainers.image.description="Demo pública del asistente bancario agéntico (modo liviano, datos ficticios)"
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=prod \
    LLM_PROVIDER=fake \
    ROUTER_BACKEND=rules \
    KNOWLEDGE_BACKEND=keyword \
    PORT=7860

# Hugging Face Spaces ejecuta el contenedor con el usuario 1000
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY backend/requirements-demo.txt backend/
RUN pip install -r backend/requirements-demo.txt

COPY backend/app backend/app
COPY data/seed data/seed
COPY --from=frontend /app/frontend/dist frontend/dist
RUN mkdir -p backend/data && chown -R app:app /app

USER app
WORKDIR /app/backend
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
