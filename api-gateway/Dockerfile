FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY api-gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy all modules
COPY api-gateway/    /app/api-gateway/
COPY verifier/       /app/verifier/
COPY rag-engine/     /app/rag-engine/
COPY self-healing-agent/ /app/self-healing-agent/
COPY mininet-runner/ /app/mininet-runner/

# Init files to make packages importable
RUN touch /app/verifier/__init__.py \
          /app/rag-engine/__init__.py \
          /app/self-healing-agent/__init__.py \
          /app/mininet-runner/__init__.py

ENV PYTHONPATH=/app
WORKDIR /app/api-gateway

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
