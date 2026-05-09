FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY api-gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY api-gateway/    /app/api-gateway/
COPY verifier/       /app/verifier/
COPY rag-engine/     /app/rag_engine/
COPY self-healing-agent/ /app/self_healing_agent/
COPY mininet-runner/ /app/mininet_runner/

RUN touch /app/verifier/__init__.py \
          /app/rag_engine/__init__.py \
          /app/self_healing_agent/__init__.py \
          /app/mininet_runner/__init__.py

ENV PYTHONPATH=/app
WORKDIR /app/api-gateway

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
