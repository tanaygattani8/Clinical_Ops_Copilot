FROM python:3.12-slim
WORKDIR /app
# README.md is copied because pyproject.toml references it for package metadata.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .
COPY . .
# Writable, host-agnostic paths — Hugging Face Spaces / Cloud Run filesystems are ephemeral,
# and /tmp is always writable regardless of which user the container runs as.
ENV CLINIC_DB_PATH=/tmp/clinic.duckdb
ENV LOG_PATH=/tmp/logs/audit.jsonl
# Use the Gemini Developer API (AI Studio key), not Vertex AI.
ENV GOOGLE_GENAI_USE_VERTEXAI=FALSE
EXPOSE 8080
# Seed the synthetic DB into the writable path at startup, then serve. Honors injected $PORT.
CMD ["sh", "-c", "python data/seed.py && uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
