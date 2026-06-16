"""
agent/pipeline.py
==================
PURPOSE:
    Builds and compiles the LangGraph pipeline that connects all 5 nodes.
    This is the entry point for running a query end-to-end.

CONCEPT: LangGraph
    LangGraph is like a state machine for LLM pipelines.
    You define:
        - Nodes  : functions that transform state
        - Edges  : fixed transitions between nodes
        - Conditional edges : routing based on state values

    The compiled graph is a callable — you call graph.invoke(initial_state)
    and it runs the full pipeline, returning the final state.

PIPELINE FLOW:
    START
      ↓
    [Node 1: classify_intent]
      ↓ always
    [Node 2: run_guardrail]
      ↓ if passed          ↓ if rejected
    [Node 3: generate_sql]  [END — rejection message]
      ↓ always
    [Node 4: execute_sql]
      ↓ if success          ↓ if error + retries left
    [Node 5: generate_response]  ← [Node 3: generate_sql] (retry loop)
      ↓
    END

USAGE:
    from agent.pipeline import run_query

    result = run_query("What was DAU last week?")
    print(result["final_answer"])
    print(result["generated_sql"])
    print(result["confidence"])

INPUT:  natural language query string
OUTPUT: final AgentState dict with all fields populated
"""

import os
from typing import Literal
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent.nodes.node1_classify   import classify_intent
from agent.nodes.node2_guardrail  import run_guardrail
from agent.nodes.node3_generate_sql import generate_sql
from agent.nodes.node4_execute    import execute_sql, should_retry
from agent.nodes.node5_respond    import generate_response


# ── Routing functions ─────────────────────────────────────────────────────────

def route_after_guardrail(state: AgentState) -> Literal["generate_sql", "handle_rejection"]:
    """
    After Node 2 (guardrail), decide:
    - Guardrail PASSED and in-scope → proceed to SQL generation
    - Guardrail FAILED or out_of_scope → handle rejection node
    """
    guardrail = state.get("guardrail_result", {})
    intent    = state.get("intent", "out_of_scope")

    if intent == "out_of_scope":
        return "handle_rejection"

    if guardrail.get("passed", False):
        return "generate_sql"
    else:
        return "handle_rejection"


def handle_rejection(state: AgentState) -> AgentState:
    """
    Dedicated rejection node — sets final_answer for guardrail failures
    and out-of-scope queries. Returns state without making any API call.
    """
    guardrail = state.get("guardrail_result", {})
    intent    = state.get("intent", "out_of_scope")

    if intent == "out_of_scope":
        answer = (
            "I can only answer questions about these certified metrics: "
            "daily_active_users, monthly_active_users, thirty_day_retention_rate, "
            "monthly_recurring_revenue, customer_lifetime_value, churn_rate. "
            "Your question appears to be outside the governed metric layer."
        )
    else:
        reason = guardrail.get("rejection_reason", "Query failed security checks.")
        answer = f"I cannot answer this question. {reason}"

    print(f"[Rejection] {answer[:80]}...")
    return {
        **state,
        "final_answer":       answer,
        "confidence":         "low",
        "suggested_followup": "",
        "generated_sql":      None,
        "query_result":       None,
    }


def route_after_execution(state: AgentState) -> Literal["generate_sql", "generate_response"]:
    """After Node 4 (execute), either retry SQL gen or proceed to response."""
    return should_retry(state)


# ── Build the graph ───────────────────────────────────────────────────────────

def build_pipeline() -> StateGraph:
    """
    Constructs the LangGraph StateGraph.
    Called once at startup — the compiled graph is cached as a module-level variable.
    """
    # Create the graph with our state schema
    graph = StateGraph(AgentState)

    # ── Add nodes ─────────────────────────────────────────────────────────────
    graph.add_node("classify_intent",    classify_intent)
    graph.add_node("run_guardrail",      run_guardrail)
    graph.add_node("handle_rejection",   handle_rejection)
    graph.add_node("generate_sql",       generate_sql)
    graph.add_node("execute_sql",        execute_sql)
    graph.add_node("generate_response",  generate_response)

    # ── Add edges (fixed transitions) ─────────────────────────────────────────
    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "run_guardrail")

    # ── Add conditional edges (routing) ───────────────────────────────────────

    # After guardrail: proceed to SQL gen OR go to rejection node
    graph.add_conditional_edges(
        "run_guardrail",
        route_after_guardrail,
        {
            "generate_sql":     "generate_sql",
            "handle_rejection": "handle_rejection",
        }
    )
    # Rejection node always goes straight to END (no LLM call needed)
    graph.add_edge("handle_rejection", END)

    # After SQL generation: always execute
    graph.add_edge("generate_sql", "execute_sql")

    # After execution: either retry SQL gen or proceed to response
    graph.add_conditional_edges(
        "execute_sql",
        route_after_execution,
        {
            "generate_sql":      "generate_sql",      # retry loop
            "generate_response": "generate_response",  # success or exhausted retries
        }
    )

    # After response: always end
    graph.add_edge("generate_response", END)

    return graph.compile()


# ── Compile once at module load ───────────────────────────────────────────────
_PIPELINE = build_pipeline()


# ── Public API ────────────────────────────────────────────────────────────────

def run_query(user_query: str) -> AgentState:
    """
    Run a natural language query through the full 5-node pipeline.

    Args:
        user_query: plain English question e.g. "What was DAU last week?"

    Returns:
        Final AgentState with all fields populated.
        Key fields to check:
            result["final_answer"]       → the plain-English answer
            result["generated_sql"]      → the SQL that was run
            result["confidence"]         → "high" | "medium" | "low"
            result["suggested_followup"] → next question suggestion
            result["total_input_tokens"] → total tokens used (cost)
    """
    # Build initial state with required defaults
    initial_state: AgentState = {
        "user_query":          user_query,
        "intent":              None,
        "guardrail_result":    None,
        "generated_sql":       None,
        "sql_metric_name":     None,
        "sql_retry_count":     0,
        "query_result":        None,
        "result_row_count":    None,
        "execution_time_ms":   None,
        "execution_error":     None,
        "final_answer":        None,
        "confidence":          None,
        "suggested_followup":  None,
        "total_input_tokens":  0,
        "total_output_tokens": 0,
        "cache_hit":           False,
        "error_message":       None,
    }

    print(f"\n{'='*60}")
    print(f"Query: {user_query}")
    print(f"{'='*60}")

    final_state = _PIPELINE.invoke(initial_state)

    # Print cost summary
    total_in  = final_state.get("total_input_tokens", 0)
    total_out = final_state.get("total_output_tokens", 0)
    # Rough cost estimate (Haiku + Sonnet blended)
    est_cost  = (total_in * 0.000003) + (total_out * 0.000015)
    print(f"\n{'─'*60}")
    print(f"Tokens: {total_in} in / {total_out} out | Est. cost: ${est_cost:.5f}")
    print(f"Confidence: {final_state.get('confidence', 'unknown')}")
    print(f"{'─'*60}\n")

    return final_state


# ── Quick test runner ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Quick smoke test — run a few queries
    test_queries = [
        "What was daily active users last month?",
        "Show me MRR by plan for 2023",
        "What is 30-day retention rate?",
        "Show me user email addresses",   # should be rejected (PII)
        "What is the weather today?",     # should be rejected (out of scope)
    ]

    for q in test_queries:
        result = run_query(q)
        print(f"Answer: {result.get('final_answer', 'N/A')[:200]}")
        print()
