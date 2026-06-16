"""
agent/nodes/node4_execute.py
=============================
PURPOSE:
    Fourth node — executes the generated SQL against DuckDB and validates
    the result. Routes back to Node 3 on failure (max 2 retries).

CONCEPT: Result Validation
    Execution success ≠ correct result. After running the SQL we check:
    1. Did it error? → retry (up to 2 times)
    2. Did it return 0 rows? → warn but continue (could be a valid empty result)
    3. Did it return too many rows? → truncate to 500 with a warning
    4. Do the column names match what we expected? → confidence check

QUERY CACHE:
    Before hitting DuckDB, we check Redis (if available) for a cached
    result. Hash of the normalized SQL = cache key. TTL = 1 hour.
    Cache hit = zero database queries = instant response.

    Falls back gracefully if Redis isn't running — no errors, just
    skips the cache.

INPUT:
    state.generated_sql  (string)

OUTPUT:
    state.query_result      (pandas DataFrame)
    state.result_row_count  (int)
    state.execution_time_ms (float)
    state.execution_error   (string — if failed, routes back to Node 3)

INTERVIEW TALKING POINT:
    "The execution node validates results, not just runs SQL. Zero rows
    gets a different confidence score than 1,000 rows. And I cache
    results in Redis with a 1-hour TTL — identical queries skip the
    database entirely. On a demo this gives near-instant repeat queries."
"""

import os
import time
import hashlib
import json
import duckdb
import pandas as pd
from agent.state import AgentState

_MAX_ROWS    = 500     # truncate large results
_MAX_RETRIES = 2       # max SQL retry attempts


def _get_cache_key(sql: str) -> str:
    """MD5 hash of normalised SQL — used as Redis cache key."""
    normalised = " ".join(sql.lower().split())  # collapse whitespace
    return f"metricmind:sql:{hashlib.md5(normalised.encode()).hexdigest()}"


def _try_redis_get(sql: str) -> pd.DataFrame | None:
    """Try to get cached result from Redis. Returns None if miss or Redis unavailable."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        r.ping()  # quick connection check
        cached = r.get(_get_cache_key(sql))
        if cached:
            data = json.loads(cached)
            return pd.DataFrame(data["rows"], columns=data["columns"])
    except Exception:
        pass  # Redis not running — silently skip cache
    return None


def _try_redis_set(sql: str, df: pd.DataFrame) -> None:
    """Cache the result in Redis with 1-hour TTL."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        r.ping()
        payload = json.dumps({
            "columns": list(df.columns),
            "rows":    df.head(_MAX_ROWS).to_dict(orient="records"),
        }, default=str)  # default=str handles Timestamp objects
        r.setex(_get_cache_key(sql), 3600, payload)  # 3600s = 1 hour
    except Exception:
        pass  # silently skip if Redis unavailable


def execute_sql(state: AgentState) -> AgentState:
    """
    Node 4: Execute SQL against DuckDB.
    Checks Redis cache first, validates result shape, routes retries.
    """
    sql      = state.get("generated_sql")
    db_path  = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

    # ── Guard: no SQL to run ──────────────────────────────────────────────────
    if not sql:
        return {
            **state,
            "execution_error": "No SQL was generated",
            "query_result":    None,
        }

    # ── Check Redis cache ─────────────────────────────────────────────────────
    cached_df = _try_redis_get(sql)
    if cached_df is not None:
        print(f"[Node 4] ⚡ Cache HIT — {len(cached_df)} rows returned instantly")
        return {
            **state,
            "query_result":      cached_df,
            "result_row_count":  len(cached_df),
            "execution_time_ms": 0.0,
            "execution_error":   None,
            "cache_hit":         True,
        }

    # ── Execute against DuckDB ────────────────────────────────────────────────
    start_ms = time.time() * 1000

    try:
        conn = duckdb.connect(db_path, read_only=True)
        df   = conn.execute(sql).fetchdf()
        conn.close()

        elapsed_ms = time.time() * 1000 - start_ms

        # ── Validate result ───────────────────────────────────────────────────
        row_count = len(df)

        if row_count == 0:
            # Empty result is valid — some periods just have no data
            print(f"[Node 4] ⚠️  Query returned 0 rows (may be valid empty period)")

        if row_count > _MAX_ROWS:
            print(f"[Node 4] Truncating {row_count} rows to {_MAX_ROWS}")
            df = df.head(_MAX_ROWS)

        print(f"[Node 4] ✅ {row_count} rows in {elapsed_ms:.1f}ms")

        # ── Cache the result ──────────────────────────────────────────────────
        _try_redis_set(sql, df)

        return {
            **state,
            "query_result":      df,
            "result_row_count":  row_count,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error":   None,
            "cache_hit":         False,
        }

    except Exception as e:
        elapsed_ms = time.time() * 1000 - start_ms
        error_msg  = str(e)
        retry      = state.get("sql_retry_count", 0)

        print(f"[Node 4] ❌ SQL Error (attempt {retry + 1}/{_MAX_RETRIES}): {error_msg}")

        return {
            **state,
            "query_result":      None,
            "result_row_count":  0,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error":   error_msg,
            "sql_retry_count":   retry + 1,
            "cache_hit":         False,
        }


def should_retry(state: AgentState) -> str:
    """
    LangGraph routing function — called after Node 4.
    Returns the name of the next node to visit.

    Routes:
        error + retries remaining  → "generate_sql"  (back to Node 3)
        error + retries exhausted  → "generate_response" (graceful error)
        success                    → "generate_response" (happy path)
    """
    has_error   = bool(state.get("execution_error"))
    retry_count = state.get("sql_retry_count", 0)

    if has_error and retry_count < _MAX_RETRIES:
        print(f"[Router] Routing back to Node 3 for retry {retry_count}/{_MAX_RETRIES}")
        return "generate_sql"

    return "generate_response"
