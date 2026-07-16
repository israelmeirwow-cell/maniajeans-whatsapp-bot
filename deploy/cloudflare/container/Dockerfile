# Full bot server as a container for Cloudflare Containers (or any Docker host).
# Slim build: no torch/whisper — semantic retrieval falls back to keyword matching and
# voice notes use the OpenAI API only if OPENAI_API_KEY is set. Everything else is identical.
FROM python:3.12-slim

WORKDIR /srv

COPY deploy/cloudflare/container/requirements-cf.txt .
RUN pip install --no-cache-dir -r requirements-cf.txt

COPY app ./app
COPY data/store ./data/store
COPY data/style ./data/style

# runtime dirs (ephemeral on Cloudflare — logs/learning reset on container restart)
RUN mkdir -p data/logs data/learning data/memory data/cache data/reports

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
