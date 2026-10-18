# Publicar la demo en línea

La imagen del `Dockerfile` de la raíz sirve frontend y API en un solo puerto, en **modo liviano**
(router por reglas, BM25 en memoria, respuestas por plantilla). No necesita GPU, Neo4j, LLM ni API
keys, y solo usa datos ficticios, así que no hay costo variable ni datos sensibles expuestos.

Pesa ~220 MB y pasa la misma prueba E2E que la versión local:

```powershell
docker build -t banking-agent-demo .
docker run -p 7860:7860 banking-agent-demo                       # http://localhost:7860
$env:BASE_URL="http://localhost:7860"; python frontend/e2e/smoke_test.py
```

## Opción A: Render (la más simple, desde GitHub)

1. Entra a https://render.com con tu cuenta de GitHub.
2. **New → Web Service** y elige el repositorio `banking-agentic-assistant`.
3. Render detecta el `Dockerfile`. Deja **Instance Type: Free** y crea el servicio.
4. En unos minutos queda en `https://<nombre>.onrender.com`. Cada push a `main` lo redespliega.

Render asigna la variable `PORT` y la imagen ya la respeta. En el plan gratuito el servicio se
duerme tras 15 minutos sin uso: la primera visita después tarda ~50 s en despertar.

## Opción B: Hugging Face Spaces (no se duerme tan rápido)

1. En https://huggingface.co/new-space crea un Space con **SDK: Docker** (plantilla en blanco).
2. Clona el Space y copia dentro: `Dockerfile`, `.dockerignore`, `backend/`, `data/`, `frontend/`.
3. El `README.md` **del Space** debe empezar con esta cabecera (no la agregues al README de GitHub):

   ```yaml
   ---
   title: Asistente Bancario Agéntico
   emoji: 🏦
   colorFrom: green
   colorTo: blue
   sdk: docker
   app_port: 7860
   ---
   ```

4. `git push` al Space. Construye la imagen y la publica en `https://huggingface.co/spaces/<usuario>/<space>`.

## Después de publicar

Agrega el enlace al inicio del README, junto al GIF. Un enlace que abre la demo en un clic es lo que
más aumenta la probabilidad de que un reclutador la pruebe.

Enlaces útiles para compartir, porque envían la pregunta al cargar:
- `https://<tu-demo>/?q=Me cobraron Bs 350 dos veces en la tarjeta de débito` (muestra el HITL)
- `https://<tu-demo>/?q=¿Cuál es mi saldo?&cliente=CLI-002`

La versión pública no incluye el router LoRA, Neo4j ni el LLM, porque requieren GPU y servicios con
costo. Los resultados con la configuración completa están en el README y en `evals/reports/`.
