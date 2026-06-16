FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    anthropic \
    langchain-anthropic \
    langchain \
    langgraph \
    langsmith \
    sqlglot \
    fastapi \
    uvicorn \
    "python-jose[cryptography]" \
    passlib \
    pydantic \
    httpx \
    redis \
    python-dotenv \
    duckdb \
    pandas \
    python-multipart \
    prometheus-client

COPY agent/   ./agent/
COPY anomaly/ ./anomaly/
COPY catalog/ ./catalog/
COPY eval/    ./eval/
COPY api/     ./api/
COPY data/    ./data/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]