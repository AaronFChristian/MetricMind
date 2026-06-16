"""
agent/nodes/node3_generate_sql.py
==================================
PURPOSE:
    Third node — generates SQL using Claude Sonnet with the full metric
    catalog injected as a cached system prompt.

CONCEPT: Prompt Caching
    The metric catalog JSON is ~3,000 tokens. Without caching, every query
    pays to process those 3,000 tokens. With Anthropic's prompt caching,
    the first call pays full price, subsequent calls pay 10% (cache hit).

    How it works:
        system prompt = [metric catalog with cache_control: "ephemeral"]
                      + [SQL generation instructions]

    Anthropic caches the catalog server-side for 5 minutes. Any query
    within that window pays only 10% of catalog token cost.

    On a demo with 20 queries in 5 minutes: saves ~$0.05 — not huge,
    but the PATTERN is what matters for your interview story.

PROMPT DESIGN:
    The prompt is tightly constrained:
    1. Only query tables in the marts schema
    2. Only use columns listed in the metric catalog
    3. Return SQL wrapped in <sql> tags — easy to parse
    4. Include a comment showing which metric was queried
    5. If you can't write safe SQL, return <cannot_answer> tags

INPUT:
    state.user_query
    state.intent
    state.guardrail_result (must be passed=True)

OUTPUT:
    state.generated_sql  (the SQL string)
    state.sql_metric_name (which certified metric)

RETRY:
    If SQL execution fails (Node 4), Node 4 sends state back here
    with execution_error set. This node rewrites the SQL incorporating
    the error message. Max 2 retries.

INTERVIEW TALKING POINT:
    "I use Anthropic's prompt caching on the metric catalog — it's 3,000
    tokens that would be re-processed on every query. With caching, I pay
    full price once per 5-minute window, then 10x cheaper after that.
    It's a small saving on a demo but a significant one at production scale."
"""

import os
import re
from anthropic import Anthropic
from agent.state import AgentState
from agent.catalog_loader import get_catalog_text, find_metric

_MODEL = "claude-sonnet-4-6"  # Sonnet for SQL generation — needs strong reasoning

# ── Build system prompt parts ─────────────────────────────────────────────────
# Split into two parts: the catalog (cached) and the instructions (not cached)
# This way only the catalog gets the cache_control header

_CATALOG_TEXT = get_catalog_text()  # loaded once at import

_SQL_INSTRUCTIONS = """You are a SQL generator for a governed analytics platform.

STRICT RULES — violate any of these and return <cannot_answer>:
1. Only query tables in the main_marts schema
2. Only use columns that exist in the metric definitions above
3. Never use SELECT * — always name specific columns
4. Never JOIN to staging or raw tables
5. Always include a comment: -- METRIC: <metric_name>
6. Wrap your SQL in <sql> tags
7. If you cannot write safe, governed SQL → return <cannot_answer>reason here</cannot_answer>

OUTPUT FORMAT:
<sql>
-- METRIC: metric_name_here
SELECT col1, col2
FROM main_marts.mart_table_name
WHERE ...
ORDER BY ... DESC
LIMIT 100
</sql>

DuckDB syntax reminders:
- Date arithmetic: date_col + INTERVAL '30 days'  (NOT dateadd)
- Current date: current_date  (NOT GETDATE(), NOT NOW())
- Date truncation: date_trunc('month', col)
- No TOP — use LIMIT instead"""


def _parse_sql(response_text: str) -> tuple[str | None, bool]:
    """
    Extracts SQL from <sql> tags or detects <cannot_answer>.
    Returns (sql_string_or_none, can_answer_bool)
    """
    # Check for cannot_answer
    if "<cannot_answer>" in response_text:
        return None, False

    # Extract from <sql> tags
    match = re.search(r"<sql>(.*?)</sql>", response_text, re.DOTALL)
    if match:
        sql = match.group(1).strip()
        return sql, True

    # Fallback: if response looks like SQL, use it directly
    if "SELECT" in response_text.upper():
        return response_text.strip(), True

    return None, False


def generate_sql(state: AgentState) -> AgentState:
    """
    Node 3: Generate SQL using Claude Sonnet with prompt caching.
    Handles first-attempt generation AND retry with error feedback.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    query        = state["user_query"]
    retry_count  = state.get("sql_retry_count", 0)
    prev_error   = state.get("execution_error")

    # ── Build user message ────────────────────────────────────────────────────
    if retry_count > 0 and prev_error:
        # Retry with error context — tell Claude what went wrong
        user_message = f"""Query: {query}

Previous SQL attempt failed with this error:
{prev_error}

Please rewrite the SQL fixing the error above.
Remember: only main_marts tables, governed columns only."""
    else:
        user_message = f"Query: {query}"

    # ── API call with prompt caching ──────────────────────────────────────────
    # The catalog block gets cache_control so Anthropic caches it server-side.
    # system is a list of blocks when using caching — not a plain string.
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=500,
            system=[
                {
                    # Block 1: the catalog — this gets cached
                    "type": "text",
                    "text": f"METRIC CATALOG (certified metrics only):\n{_CATALOG_TEXT}",
                    "cache_control": {"type": "ephemeral"}
                },
                {
                    # Block 2: SQL instructions — not cached (small, changes rarely)
                    "type": "text",
                    "text": _SQL_INSTRUCTIONS
                }
            ],
            messages=[{"role": "user", "content": user_message}]
        )

        raw_text      = response.content[0].text
        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        # Log cache performance
        cache_creation = getattr(response.usage, "cache_creation_input_tokens", 0)
        cache_read     = getattr(response.usage, "cache_read_input_tokens", 0)
        if cache_read > 0:
            print(f"[Node 3] 🎯 Cache HIT — {cache_read} tokens read from cache (saved ~{cache_read * 0.000003 * 0.9:.4f}$)")
        elif cache_creation > 0:
            print(f"[Node 3] 📝 Cache MISS — {cache_creation} tokens written to cache")

        # Parse the SQL from the response
        generated_sql, can_answer = _parse_sql(raw_text)

        if not can_answer:
            print(f"[Node 3] Model returned cannot_answer")
            return {
                **state,
                "generated_sql":   None,
                "error_message":   "The model determined this query cannot be answered from governed metrics.",
                "total_input_tokens":  state.get("total_input_tokens",  0) + input_tokens,
                "total_output_tokens": state.get("total_output_tokens", 0) + output_tokens,
            }

        # Try to identify which metric was queried
        metric_name = find_metric(query)
        # Also try to extract from the SQL comment -- METRIC: xxx
        metric_match = re.search(r"--\s*METRIC:\s*(\w+)", generated_sql or "")
        if metric_match:
            metric_name = metric_match.group(1)

        print(f"[Node 3] SQL generated ({len(generated_sql)} chars) | Metric: {metric_name} | Tokens: {input_tokens}in/{output_tokens}out")

        return {
            **state,
            "generated_sql":    generated_sql,
            "sql_metric_name":  metric_name,
            "execution_error":  None,  # clear previous error on retry
            "total_input_tokens":  state.get("total_input_tokens",  0) + input_tokens,
            "total_output_tokens": state.get("total_output_tokens", 0) + output_tokens,
        }

    except Exception as e:
        print(f"[Node 3] Error: {e}")
        return {
            **state,
            "generated_sql": None,
            "error_message": f"SQL generation failed: {e}",
        }
