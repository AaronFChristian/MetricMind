"""
api/main.py
============
PURPOSE:
    FastAPI backend that exposes the LangGraph agent pipeline as a REST API.
    The React frontend calls these endpoints.

ENDPOINTS:
    POST /api/query      → run a natural language query through the pipeline
    GET  /api/metrics    → return the certified metrics catalog
    GET  /metrics        → Prometheus metrics (scraped by Prometheus every 15s)
    GET  /api/anomalies  → return the anomaly feed from DuckDB
    GET  /api/health     → health check for Railway deployment

HOW TO RUN LOCALLY:
    uvicorn api.main:app --reload --port 8000
    Then open: http://localhost:8000/docs

CONCEPT:
    FastAPI automatically generates OpenAPI docs from type hints.
    Every endpoint is async. Rate limiting prevents runaway API costs.
    Prometheus metrics are exported at /metrics for Grafana dashboards.
"""

import os
import time
import json
import duckdb
from datetime import datetime
from typing import Optional
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# ── Prometheus metrics ────────────────────────────────────────────────────────
from prometheus_client import (
    generate_latest, CONTENT_TYPE_LATEST,
    Counter, Histogram, Gauge
)

# Define all metrics at module level (must be created once)
QUERIES_TOTAL = Counter(
    "metricmind_queries_total",
    "Total queries processed",
    ["intent", "confidence"]
)
GUARDRAIL_REJECTIONS = Counter(
    "metricmind_guardrail_rejections_total",
    "Guardrail rejections by reason",
    ["reason"]
)
CACHE_HITS = Counter(
    "metricmind_cache_hits_total",
    "Cache hits vs misses",
    ["result"]
)
SQL_RETRIES = Counter(
    "metricmind_sql_retries_total",
    "SQL generation retries"
)
QUERY_DURATION = Histogram(
    "metricmind_query_duration_seconds",
    "End-to-end query latency",
    ["intent"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0]
)
TOKEN_COST = Histogram(
    "metricmind_token_cost_dollars",
    "Estimated cost per query in USD",
    buckets=[0.001, 0.002, 0.005, 0.01, 0.02, 0.05]
)
ACTIVE_REQUESTS = Gauge(
    "metricmind_active_requests",
    "In-flight requests"
)

load_dotenv()

# ── Import agent pipeline ─────────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.pipeline import run_query
from agent.catalog_loader import _CATALOG

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MetricMind API",
    description="Governed text-to-SQL analytics API powered by Claude",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://*.vercel.app",
        os.environ.get("FRONTEND_URL", "http://localhost:3000"),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
_rate_limit_store: dict = defaultdict(list)
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW   = 3600


def check_rate_limit(request: Request):
    client_ip    = request.client.host
    now          = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    _rate_limit_store[client_ip] = [
        ts for ts in _rate_limit_store[client_ip]
        if ts > window_start
    ]

    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_REQUESTS} queries per hour."
        )
    _rate_limit_store[client_ip].append(now)


# ── Prometheus helper ─────────────────────────────────────────────────────────
def record_query_metrics(
    intent: str,
    confidence: str,
    duration_sec: float,
    est_cost: float,
    cache_hit: bool,
    was_rejected: bool,
    rejection_reason: Optional[str],
    retry_count: int
):
    """Record all Prometheus metrics for a completed query."""
    if was_rejected:
        reason = (rejection_reason or "").lower()
        if "pii" in reason or "email" in reason or "phone" in reason:
            GUARDRAIL_REJECTIONS.labels(reason="pii").inc()
        elif "injection" in reason or "drop" in reason or "delete" in reason:
            GUARDRAIL_REJECTIONS.labels(reason="injection").inc()
        elif "scope" in reason or "out_of_scope" in (intent or ""):
            GUARDRAIL_REJECTIONS.labels(reason="out_of_scope").inc()
        else:
            GUARDRAIL_REJECTIONS.labels(reason="metric_allowlist").inc()
    else:
        QUERIES_TOTAL.labels(
            intent=intent or "unknown",
            confidence=confidence or "unknown"
        ).inc()
        QUERY_DURATION.labels(intent=intent or "unknown").observe(duration_sec)
        TOKEN_COST.observe(est_cost)

    CACHE_HITS.labels(result="hit" if cache_hit else "miss").inc()

    if retry_count > 0:
        SQL_RETRIES.inc(retry_count)


# ── Models ────────────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str

    class Config:
        json_schema_extra = {
            "example": {"question": "What was DAU last month by country?"}
        }


class QueryResponse(BaseModel):
    question:           str
    answer:             str
    sql:                Optional[str]
    confidence:         str
    suggested_followup: Optional[str]
    metric_name:        Optional[str]
    row_count:          Optional[int]
    execution_time_ms:  Optional[float]
    tokens_used:        int
    estimated_cost:     float
    cache_hit:          bool
    intent:             str
    guardrail_passed:   bool
    result_data:        Optional[list]
    timestamp:          str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    """Health check — Railway pings this to verify the service is up."""
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")
    db_ok   = os.path.exists(db_path)
    return {
        "status":    "healthy",
        "timestamp": datetime.now().isoformat(),
        "database":  "connected" if db_ok else "not found",
        "version":   "1.0.0",
    }


@app.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus metrics endpoint.
    Scraped by Prometheus every 15 seconds.
    Grafana reads from Prometheus to build the LLMOps dashboard.
    """
    return FastAPIResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.post("/api/query", response_model=QueryResponse)
async def query_metrics(
    body:    QueryRequest,
    request: Request,
    _:       None = Depends(check_rate_limit),
):
    """
    Main endpoint — runs NL query through the 5-node LangGraph pipeline.
    Records Prometheus metrics on every call.
    Rate limited to 20 requests per IP per hour.
    """
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    if len(body.question) > 500:
        raise HTTPException(status_code=400, detail="Question too long (max 500 chars)")

    # Track in-flight requests (Gauge goes up at start, down at end)
    ACTIVE_REQUESTS.inc()
    _start = time.time()

    try:
        result = run_query(body.question)

        # Serialize DataFrame result
        result_data = None
        df = result.get("query_result")
        if df is not None and len(df) > 0:
            result_data = df.head(50).to_dict(orient="records")
            for row in result_data:
                for k, v in row.items():
                    if hasattr(v, "isoformat"):
                        row[k] = v.isoformat()
                    elif hasattr(v, "item"):
                        row[k] = v.item()

        total_tokens = (
            result.get("total_input_tokens", 0) +
            result.get("total_output_tokens", 0)
        )
        est_cost = (
            result.get("total_input_tokens", 0) * 0.000003 +
            result.get("total_output_tokens", 0) * 0.000015
        )

        guardrail    = result.get("guardrail_result") or {}
        duration_sec = time.time() - _start

        # ── Record Prometheus metrics ─────────────────────────────────────────
        record_query_metrics(
            intent           = result.get("intent", "unknown"),
            confidence       = result.get("confidence", "unknown"),
            duration_sec     = duration_sec,
            est_cost         = round(est_cost, 5),
            cache_hit        = result.get("cache_hit", False),
            was_rejected     = not guardrail.get("passed", False),
            rejection_reason = guardrail.get("rejection_reason"),
            retry_count      = result.get("sql_retry_count", 0)
        )

        return QueryResponse(
            question           = body.question,
            answer             = result.get("final_answer", "No answer generated"),
            sql                = result.get("generated_sql"),
            confidence         = result.get("confidence", "low"),
            suggested_followup = result.get("suggested_followup"),
            metric_name        = result.get("sql_metric_name"),
            row_count          = result.get("result_row_count"),
            execution_time_ms  = result.get("execution_time_ms"),
            tokens_used        = total_tokens,
            estimated_cost     = round(est_cost, 5),
            cache_hit          = result.get("cache_hit", False),
            intent             = result.get("intent", "unknown"),
            guardrail_passed   = guardrail.get("passed", False),
            result_data        = result_data,
            timestamp          = datetime.now().isoformat(),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

    finally:
        # Always decrement — even if the request errored
        ACTIVE_REQUESTS.dec()


@app.get("/api/metrics")
async def get_metrics_catalog():
    """Returns the certified metrics catalog for the React frontend."""
    return {
        "metrics": _CATALOG.get("certified_metrics", []),
        "total":   len(_CATALOG.get("certified_metrics", [])),
        "version": _CATALOG.get("_meta", {}).get("version", "1.0.0"),
    }


@app.get("/api/anomalies")
async def get_anomalies():
    """Returns detected anomalies from the DuckDB anomaly_feed table."""
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

    try:
        conn   = duckdb.connect(db_path, read_only=True)
        tables = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'anomaly_feed'
        """).fetchdf()

        if tables.empty:
            conn.close()
            return {
                "anomalies": [], "total": 0,
                "message": "No anomalies yet. Run: python anomaly/detector.py"
            }

        anomalies = conn.execute("""
            SELECT * FROM main.anomaly_feed
            ORDER BY detected_at DESC LIMIT 20
        """).fetchdf()
        conn.close()

        return {
            "anomalies": anomalies.to_dict(orient="records"),
            "total":     len(anomalies),
        }

    except Exception as e:
        return {"anomalies": [], "total": 0, "error": str(e)}


@app.post("/api/anomalies/{anomaly_id}/approve")
async def approve_anomaly_commentary(anomaly_id: int):
    """Human-in-the-loop: approve anomaly commentary for publishing."""
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

    try:
        conn = duckdb.connect(db_path)
        conn.execute("""
            UPDATE main.anomaly_feed
            SET commentary_approved = true
            WHERE id = ?
        """, [anomaly_id])
        conn.close()
        return {"status": "approved", "anomaly_id": anomaly_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_usage_stats():
    """Returns query statistics from the guardrail audit log."""
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

    try:
        conn  = duckdb.connect(db_path, read_only=True)
        stats = conn.execute("""
            SELECT
                COUNT(*)                                     as total_queries,
                SUM(CASE WHEN passed     THEN 1 ELSE 0 END) as passed_queries,
                SUM(CASE WHEN NOT passed THEN 1 ELSE 0 END) as rejected_queries
            FROM main.guardrail_audit_log
        """).fetchone()
        conn.close()

        return {
            "total_queries":    stats[0] if stats else 0,
            "passed_queries":   stats[1] if stats else 0,
            "rejected_queries": stats[2] if stats else 0,
        }
    except Exception:
        return {"total_queries": 0, "passed_queries": 0, "rejected_queries": 0}