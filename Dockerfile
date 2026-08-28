# Still a floating tag. Pinning by digest is the correct fix and belongs here -
#   FROM python:3.12-slim@sha256:<digest from `docker buildx imagetools inspect`>
# - but the digest has to be read from the registry, not guessed, so it is left for
# a build that can verify it. Python deps are already pinned in pyproject.toml,
# which is where the outage actually came from.
FROM python:3.12-slim
WORKDIR /app
# Copy the whole tree BEFORE installing. pyproject.toml now declares real packages,
# and setuptools cannot find directories that have not been copied yet - installing
# from pyproject.toml alone failed the build. This costs the dependency-layer cache
# on every code change, which is the right trade for an install that actually
# installs the project. README.md is needed here too: pyproject references it.
COPY . .
RUN pip install --no-cache-dir .
# Writable, host-agnostic paths — Hugging Face Spaces / Cloud Run filesystems are ephemeral,
# and /tmp is always writable regardless of which user the container runs as.
# CLINIC_AUDIT_DIR can be pointed at a mounted volume to keep the audit trail across restarts.
ENV CLINIC_DB_PATH=/tmp/clinic.duckdb
ENV LOG_PATH=/tmp/logs/audit.jsonl
# Use the Gemini Developer API (AI Studio key), not Vertex AI.
ENV GOOGLE_GENAI_USE_VERTEXAI=FALSE
# Running as root is a real finding, but the obvious fix is not portable here:
# `useradd --uid 1000 appuser` failed the Hugging Face build, and iterating on a
# uid collision against a live demo is not worth the downtime. Revisit with a
# reproducible local `docker build` that can show the actual useradd error.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/health',timeout=4).status==200 else 1)"
# Seed the synthetic DB into the writable path at startup, then serve. Honors injected $PORT.
CMD ["sh", "-c", "python data/seed.py && uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
