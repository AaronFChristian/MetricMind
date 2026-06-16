"""
agent/nodes/node2_guardrail.py
===============================
PURPOSE:
    Second node — the security and governance gate.
    Runs 3 checks before any SQL is generated.
    Uses Claude Haiku (cheap) + deterministic regex (free).

CONCEPT:
    Three-layer guardrail:

    Layer 1 — Metric allowlist (LLM-based):
        Does the query reference only metrics in the certified catalog?
        If someone asks "show me raw user emails" or "query the payments table
        directly" — rejected here.

    Layer 2 — PII check (deterministic):
        Does the query mention any PII column names (email, name, address)?
        Regex-based — no LLM needed, zero cost, zero latency.

    Layer 3 — SQL injection check (deterministic):
        Does the query contain SQL injection patterns (DROP, DELETE, --, etc.)?
        Regex-based.

    Every rejection is logged to an audit_log table in DuckDB.
    This is your security story for the portfolio.

INPUT:
    state.user_query  (string)
    state.intent      (string)

OUTPUT:
    state.guardrail_result (GuardrailResult dict)

AUDIT LOG:
    Every guardrail hit (pass OR fail) is written to DuckDB audit_log table.
    Columns: timestamp, query_text, intent, passed, failure_reason

INTERVIEW TALKING POINT:
    "The guardrail node runs before any SQL generation. Even if someone
    tries to jailbreak the prompt, they can't get past the regex checks
    for PII columns and SQL injection. Every rejection is logged with
    a timestamp and reason — full audit trail."
"""

import os
import re
import json
import duckdb
from datetime import datetime
from anthropic import Anthropic
from agent.state import AgentState, GuardrailResult
from agent.catalog_loader import (
    get_metric_names, is_pii_column,
    get_rejection_message, get_forbidden_operations
)

_MODEL = "claude-haiku-4-5"  # cheap — guardrail is a simple check

# ── PII patterns — deterministic regex (no LLM needed) ───────────────────────
_PII_PATTERNS = [
    r'\bemail\b', r'\bphone\b', r'\baddress\b',
    r'\bfull.?name\b', r'\bssn\b', r'\bpassword\b',
    r'\bcredit.?card\b', r'\bip.?address\b',
]

# ── SQL injection patterns ────────────────────────────────────────────────────
_INJECTION_PATTERNS = [
    r'\bDROP\b', r'\bDELETE\b', r'\bTRUNCATE\b', r'\bALTER\b',
    r'\bINSERT\b', r'\bUPDATE\b', r'\bEXEC\b', r'\bEXECUTE\b',
    r'--', r'/\*', r'\bUNION\b.*\bSELECT\b', r';\s*SELECT',
    r"'\s*OR\s*'", r'1\s*=\s*1',
]

# ── Guardrail system prompt ────────────────────────────────────────────────────
_METRIC_NAMES = get_metric_names()
_SYSTEM_PROMPT = f"""You are a security guardrail for an analytics platform.

Certified metrics that are allowed: {", ".join(_METRIC_NAMES)}

Important: customer_lifetime_value includes LTV, CLV, LTV:CAC ratio, and CAC (customer acquisition cost) — these are all columns inside the certified mart table. Treat any question about LTV, CAC, or LTV:CAC as certified.

Check if the query ONLY asks about the certified metrics above.
Return ONLY this JSON — no other text:
{{
  "metric_check": true/false,
  "reason": "one sentence explanation"
}}

metric_check = true  → query is about certified metrics only
metric_check = false → query tries to access uncertified data, raw tables, or invented metrics"""


def _check_pii(query: str) -> tuple[bool, str]:
    """Deterministic PII check. Returns (passed, reason)."""
    query_lower = query.lower()
    for pattern in _PII_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            return False, f"Query references PII data: matched pattern '{pattern}'"
    return True, ""


def _check_injection(query: str) -> tuple[bool, str]:
    """Deterministic SQL injection check. Returns (passed, reason)."""
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return False, f"Potential SQL injection detected: '{pattern}'"
    return True, ""


def _log_to_audit(
    query: str, intent: str, passed: bool,
    failure_reason: str, db_path: str
) -> None:
    """
    Write every guardrail decision to the audit log.
    Creates the table if it doesn't exist.
    This is the full audit trail — every query attempt recorded.
    """
    try:
        conn = duckdb.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS main.guardrail_audit_log (
                id          INTEGER DEFAULT(abs(random()) % 1000000),
                checked_at  TIMESTAMP,
                query_text  VARCHAR,
                intent      VARCHAR,
                passed      BOOLEAN,
                failure_reason VARCHAR
            )
        """)
        conn.execute("""
            INSERT INTO main.guardrail_audit_log
                (checked_at, query_text, intent, passed, failure_reason)
            VALUES (?, ?, ?, ?, ?)
        """, [datetime.now(), query[:500], intent, passed, failure_reason])
        conn.close()
    except Exception as e:
        # Audit log failure should never block the pipeline
        print(f"[Node 2] Audit log warning: {e}")


def run_guardrail(state: AgentState) -> AgentState:
    """
    Node 2: Three-layer security + governance check.
    Cheap Haiku call for metric check + free regex for PII + injection.
    """
    query  = state["user_query"]
    intent = state.get("intent", "out_of_scope")
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

    # ── Layer 1: PII check (deterministic, free) ──────────────────────────────
    pii_passed, pii_reason = _check_pii(query)

    # ── Layer 2: SQL injection check (deterministic, free) ────────────────────
    injection_passed, injection_reason = _check_injection(query)

    # ── Layer 3: Metric allowlist check (LLM — Haiku) ─────────────────────────
    metric_passed = True
    metric_reason = ""
    input_tokens  = 0
    output_tokens = 0

    # Skip metric check for out_of_scope — already rejected by node 1
    if intent != "out_of_scope" and pii_passed and injection_passed:
        try:
            client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            response = client.messages.create(
                model=_MODEL,
                max_tokens=100,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": query}]
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json")
            result = json.loads(raw)
            metric_passed = result.get("metric_check", False)
            metric_reason = result.get("reason", "")
            input_tokens  = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        except Exception as e:
            print(f"[Node 2] Metric check error: {e} — defaulting to fail-safe")
            metric_passed = False
            metric_reason = f"Guardrail check failed: {e}"

    # ── Assemble result ───────────────────────────────────────────────────────
    overall_passed = pii_passed and injection_passed and metric_passed

    # Build rejection reason (first failure wins)
    rejection_reason = None
    if not pii_passed:
        rejection_reason = pii_reason
    elif not injection_passed:
        rejection_reason = injection_reason
    elif not metric_passed:
        rejection_reason = (
            metric_reason or get_rejection_message()
        )

    guardrail_result: GuardrailResult = {
        "passed":           overall_passed,
        "metric_check":     metric_passed,
        "pii_check":        pii_passed,
        "injection_check":  injection_passed,
        "rejection_reason": rejection_reason,
    }

    # ── Audit log ─────────────────────────────────────────────────────────────
    _log_to_audit(
        query=query,
        intent=intent,
        passed=overall_passed,
        failure_reason=rejection_reason or "",
        db_path=db_path
    )

    status = "✅ PASSED" if overall_passed else f"❌ REJECTED: {rejection_reason}"
    print(f"[Node 2] Guardrail: {status} | Tokens: {input_tokens}in/{output_tokens}out")

    return {
        **state,
        "guardrail_result": guardrail_result,
        "total_input_tokens":  state.get("total_input_tokens",  0) + input_tokens,
        "total_output_tokens": state.get("total_output_tokens", 0) + output_tokens,
    }
