"""
api/metrics.py
==============
PURPOSE:
    Exports Prometheus metrics from the FastAPI backend.
    Grafana scrapes these every 15 seconds to build live dashboards.

METRICS EXPORTED:
    metricmind_queries_total          - total queries by intent and confidence
    metricmind_query_duration_seconds - latency histogram per query
    metricmind_guardrail_rejections   - rejections by reason (PII, injection, scope)
    metricmind_token_cost_dollars     - running token cost per query
    metricmind_cache_hits_total       - Redis cache hits vs misses
    metricmind_sql_retries_total      - SQL retry count (measures SQL gen quality)
    metricmind_active_requests        - in-flight requests (live load gauge)

HOW TO VIEW:
    Start FastAPI: uvicorn api.main:app --port 8000
    Open: http://localhost:8000/metrics
    You will see raw Prometheus text format.

GRAFANA SETUP:
    1. Run: docker compose up (starts Grafana + Prometheus)
    2. Open: http://localhost:3001 (Grafana)
    3. Login: admin / metricmind
    4. Dashboard auto-loads from grafana/dashboards/metricmind.json
"""

from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
)
from fastapi import Response

# ── Counters (always go up) ───────────────────────────────────────────────────

queries_total = Counter(
    "metricmind_queries_total",
    "Total number of queries processed",
    ["intent", "confidence"]          # labels: intent=metric_query, confidence=high
)

guardrail_rejections_total = Counter(
    "metricmind_guardrail_rejections_total",
    "Total guardrail rejections by reason",
    ["reason"]                        # labels: reason=pii|injection|out_of_scope|metric
)

cache_hits_total = Counter(
    "metricmind_cache_hits_total",
    "Redis query result cache hits",
    ["result"]                        # labels: result=hit|miss
)

sql_retries_total = Counter(
    "metricmind_sql_retries_total",
    "SQL generation retries (measures Node 3 quality)"
)

# ── Histograms (track distributions) ─────────────────────────────────────────

query_duration_seconds = Histogram(
    "metricmind_query_duration_seconds",
    "End-to-end query latency in seconds",
    ["intent"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0]
)

token_cost_dollars = Histogram(
    "metricmind_token_cost_dollars",
    "Estimated cost per query in USD",
    buckets=[0.001, 0.002, 0.005, 0.01, 0.02, 0.05]
)

# ── Gauges (can go up or down) ────────────────────────────────────────────────

active_requests = Gauge(
    "metricmind_active_requests",
    "Number of queries currently being processed"
)

# ── Metrics endpoint ──────────────────────────────────────────────────────────

def get_metrics_endpoint():
    """Returns raw Prometheus text for scraping."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


# ── Helper: record a completed query ─────────────────────────────────────────

def record_query(
    intent: str,
    confidence: str,
    duration_sec: float,
    est_cost: float,
    cache_hit: bool,
    was_rejected: bool,
    rejection_reason: str | None,
    retry_count: int
):
    """
    Call this after every query completes to record all metrics at once.
    Called from api/main.py in the /api/query endpoint.
    """
    if was_rejected:
        reason = rejection_reason or "unknown"
        if "pii" in reason.lower() or "email" in reason.lower():
            guardrail_rejections_total.labels(reason="pii").inc()
        elif "injection" in reason.lower():
            guardrail_rejections_total.labels(reason="injection").inc()
        elif "scope" in reason.lower() or "out_of_scope" in (intent or ""):
            guardrail_rejections_total.labels(reason="out_of_scope").inc()
        else:
            guardrail_rejections_total.labels(reason="metric_allowlist").inc()
    else:
        queries_total.labels(
            intent=intent or "unknown",
            confidence=confidence or "unknown"
        ).inc()
        query_duration_seconds.labels(intent=intent or "unknown").observe(duration_sec)
        token_cost_dollars.observe(est_cost)

    cache_hits_total.labels(result="hit" if cache_hit else "miss").inc()

    if retry_count > 0:
        sql_retries_total.inc(retry_count)
