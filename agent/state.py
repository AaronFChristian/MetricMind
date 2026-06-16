"""
agent/state.py
==============
PURPOSE:
    Defines the AgentState TypedDict — the single shared object that gets
    passed between every node in the LangGraph pipeline.

CONCEPT:
    LangGraph works like an assembly line. Each node receives the full state,
    does its job, and returns ONLY the fields it changed. LangGraph merges
    the changes back automatically.

    Think of AgentState as a "ticket" that travels through the pipeline:
    Node 1 fills in 'intent', Node 2 fills in 'guardrail_result',
    Node 3 fills in 'generated_sql', etc.

INPUT:  nothing — this is just a type definition
OUTPUT: nothing — imported by every node and the pipeline builder

INTERVIEW TALKING POINT:
    "I used TypedDict for the state so every field is documented and
    type-checked. When something breaks, I know exactly which node
    set which field and what type it should be."
"""

from typing import TypedDict, Optional, Any
import pandas as pd


class GuardrailResult(TypedDict):
    """Result from the guardrail check node."""
    passed: bool                    # True = safe to proceed
    metric_check: bool              # query references only certified metrics
    pii_check: bool                 # no PII columns requested
    injection_check: bool           # no SQL injection patterns
    rejection_reason: Optional[str] # human-readable reason if passed=False


class AgentState(TypedDict):
    """
    The complete state object that flows through all 5 pipeline nodes.
    Fields are filled in progressively as the query moves through the pipeline.

    Flow:
        user_query
            → [Node 1: classify]    → intent
            → [Node 2: guardrail]   → guardrail_result
            → [Node 3: generate]    → generated_sql
            → [Node 4: execute]     → query_result, execution_error
            → [Node 5: respond]     → final_answer, confidence, suggested_followup
    """

    # ── Input (set by the caller before pipeline starts) ─────────────────────
    user_query: str                         # the raw natural language question

    # ── Node 1: classify_intent ──────────────────────────────────────────────
    intent: Optional[str]                   # one of: metric_query, cohort_analysis,
                                            # anomaly_check, memo_request, out_of_scope

    # ── Node 2: guardrail_check ──────────────────────────────────────────────
    guardrail_result: Optional[GuardrailResult]

    # ── Node 3: generate_sql ─────────────────────────────────────────────────
    generated_sql: Optional[str]            # the SQL to run against DuckDB
    sql_metric_name: Optional[str]          # which certified metric this SQL queries
    sql_retry_count: int                    # how many times we've retried (max 2)

    # ── Node 4: validate_and_execute ─────────────────────────────────────────
    query_result: Optional[Any]             # pandas DataFrame or None
    result_row_count: Optional[int]
    execution_time_ms: Optional[float]
    execution_error: Optional[str]          # SQL error message if execution failed

    # ── Node 5: generate_response ────────────────────────────────────────────
    final_answer: Optional[str]             # plain-English answer shown to user
    confidence: Optional[str]              # "high" | "medium" | "low"
    suggested_followup: Optional[str]       # one follow-up question to show in UI

    # ── Metadata (set throughout) ─────────────────────────────────────────────
    total_input_tokens: int                 # running token count (cost tracking)
    total_output_tokens: int
    cache_hit: bool                         # True if result came from Redis cache
    error_message: Optional[str]           # pipeline-level error (not SQL error)
