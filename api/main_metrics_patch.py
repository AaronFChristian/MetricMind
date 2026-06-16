"""
HOW TO ADD PROMETHEUS TO api/main.py
=====================================
Add these 3 changes to your existing api/main.py:

CHANGE 1: Add import at the top (after other imports)
-----------------------------------------------------
from api.metrics import (
    get_metrics_endpoint, record_query,
    active_requests
)

CHANGE 2: Add /metrics endpoint (after /api/health)
----------------------------------------------------
@app.get("/metrics")
async def metrics():
    return get_metrics_endpoint()

CHANGE 3: Wrap the /api/query endpoint with metrics recording
-------------------------------------------------------------
In the query_metrics function, add at the start:
    active_requests.inc()

Add at the end (before the return statement):
    active_requests.dec()
    record_query(
        intent           = result.get("intent", "unknown"),
        confidence       = result.get("confidence", "unknown"),
        duration_sec     = (result.get("execution_time_ms") or 0) / 1000,
        est_cost         = round(est_cost, 5),
        cache_hit        = result.get("cache_hit", False),
        was_rejected     = not guardrail.get("passed", False),
        rejection_reason = guardrail.get("rejection_reason"),
        retry_count      = result.get("sql_retry_count", 0)
    )

CHANGE 4: Add prometheus-client to your pip install
----------------------------------------------------
pip install prometheus-client
"""
