"""
agent/nodes/node1_classify.py
==============================
PURPOSE:
    First node in the pipeline. Classifies the user's query into one of
    5 intent categories using Claude Haiku (the cheapest model — ~$0.00025
    per call vs $0.003 for Sonnet).

CONCEPT:
    Before doing any expensive SQL generation, we quickly check: "what kind
    of question is this?" If it's out_of_scope, we terminate immediately
    and never make a Sonnet call — saving ~90% of cost for bad queries.

    This is the "cheap filter before expensive work" pattern used in
    production LLM systems.

INPUT:
    state.user_query  (string)

OUTPUT:
    state.intent  — one of:
        "metric_query"     → standard metric question (DAU, MRR, retention etc.)
        "cohort_analysis"  → cohort or segment comparison
        "anomaly_check"    → "why did X drop?" or "is X unusual?"
        "memo_request"     → "write me a summary" or "draft a memo"
        "out_of_scope"     → anything not answerable from governed metrics

TOKEN COST:
    ~150 input tokens + ~10 output tokens per call
    Using Haiku: ~$0.00003 per classification

INTERVIEW TALKING POINT:
    "I use Haiku for classification because the task is simple — just pick
    one of 5 buckets. No need to pay Sonnet prices for a routing decision.
    Out-of-scope queries are rejected here before any SQL generation happens."
"""

import os
import json
from anthropic import Anthropic
from agent.state import AgentState
from agent.catalog_loader import get_metric_names

# Haiku is ~20x cheaper than Sonnet — perfect for simple classification
_MODEL = "claude-haiku-4-5"

# Build the metric names list once (used in the prompt)
_METRIC_NAMES = get_metric_names()

# ── System prompt ──────────────────────────────────────────────────────────────
# Short and focused — Haiku doesn't need long prompts for classification
_SYSTEM_PROMPT = f"""You are a query classifier for an analytics platform.

Certified metrics: {", ".join(_METRIC_NAMES)}

Common metric phrases that ARE certified (classify as metric_query):
- LTV to CAC, LTV:CAC, LTV CAC ratio → customer_lifetime_value
- DAU, daily actives, active users → daily_active_users
- MAU, monthly actives → monthly_active_users
- MRR, monthly revenue, recurring revenue → monthly_recurring_revenue
- churn, cancellation rate → churn_rate
- retention, day-30, 30-day → thirty_day_retention_rate

Classify the user's query into EXACTLY ONE of these categories:
- metric_query      : asks about a certified metric (DAU, MAU, MRR, retention, churn, LTV, CAC)
- cohort_analysis   : compares groups, segments, or cohorts
- anomaly_check     : asks why a metric changed, dropped, or spiked
- memo_request      : asks for a summary, report, or written memo
- out_of_scope      : anything not answerable from the certified metrics above

Return ONLY a JSON object with this exact format:
{{"intent": "<category>", "reasoning": "<one sentence>"}}

No other text. No markdown. Just the JSON object."""


def classify_intent(state: AgentState) -> AgentState:
    """
    Node 1: Classify user query intent.
    Uses Claude Haiku for cost efficiency (~$0.00003 per call).
    Terminates pipeline immediately for out_of_scope queries.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=100,  # classification needs very few tokens
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": state["user_query"]}
            ]
        )

        # Parse the JSON response
        raw_text = response.content[0].text.strip()

        # Strip markdown fences if model adds them despite instructions
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        result = json.loads(raw_text)
        intent = result.get("intent", "out_of_scope")

        # Validate — only accept known intents
        valid_intents = {
            "metric_query", "cohort_analysis",
            "anomaly_check", "memo_request", "out_of_scope"
        }
        if intent not in valid_intents:
            intent = "out_of_scope"

        # Track token usage for cost dashboard
        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        print(f"[Node 1] Intent: {intent} | Tokens: {input_tokens}in/{output_tokens}out")

        return {
            **state,
            "intent": intent,
            "total_input_tokens":  state.get("total_input_tokens",  0) + input_tokens,
            "total_output_tokens": state.get("total_output_tokens", 0) + output_tokens,
        }

    except json.JSONDecodeError:
        # If Haiku returns garbage, default to out_of_scope (safe fallback)
        print(f"[Node 1] JSON parse failed — defaulting to out_of_scope")
        return {**state, "intent": "out_of_scope"}

    except Exception as e:
        print(f"[Node 1] Error: {e}")
        return {**state, "intent": "out_of_scope", "error_message": str(e)}
