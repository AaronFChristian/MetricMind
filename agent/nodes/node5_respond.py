"""
agent/nodes/node5_respond.py
=============================
PURPOSE:
    Fifth and final node — takes the SQL result (a DataFrame) and generates
    a plain-English answer using Claude Sonnet.

CONCEPT:
    The response has four components:
    1. Plain-English answer (the actual answer to the user's question)
    2. Confidence level (high/medium/low — based on data shape)
    3. Suggested follow-up (one question to guide exploration)
    4. Summary of SQL run (shown in UI "Show SQL" toggle)

    Confidence scoring logic (deterministic — no LLM needed):
    - high:   result has rows, columns match expected, no errors
    - medium: result has rows but row count is suspiciously low or high
    - low:    0 rows, execution errors, or retry was needed

    The answer is SHORT and direct. SaaS leaders don't want paragraphs —
    they want "DAU was 342 last Tuesday, down 12% from the prior week."

INPUT:
    state.query_result      (pandas DataFrame)
    state.generated_sql     (shown in UI)
    state.user_query        (original question)
    state.execution_error   (if set, generates graceful error response)

OUTPUT:
    state.final_answer       (string — shown to user)
    state.confidence         ("high" | "medium" | "low")
    state.suggested_followup (string — next question suggestion)

INTERVIEW TALKING POINT:
    "The confidence score is deterministic — I don't ask the LLM to
    judge its own output because LLMs are overconfident. Instead I score
    based on data shape: 0 rows = low confidence, result matches expected
    columns = high confidence. The LLM just writes the sentence."
"""

import os
import json
import pandas as pd
from anthropic import Anthropic
from agent.state import AgentState

_MODEL = "claude-sonnet-4-6"


def _score_confidence(state: AgentState) -> str:
    """
    Deterministic confidence scoring.
    Does NOT ask the LLM — LLMs are overconfident about their own outputs.
    """
    if state.get("execution_error"):
        return "low"

    row_count   = state.get("result_row_count", 0)
    retry_count = state.get("sql_retry_count", 0)

    if row_count == 0:
        return "low"    # empty result — uncertain

    if retry_count > 0:
        return "medium"  # needed a retry — something was off

    if row_count > 200:
        return "medium"  # large result — might be returning too much

    return "high"        # clean result, first try, reasonable size


def _df_to_prompt_string(df: pd.DataFrame, max_rows: int = 20) -> str:
    """
    Converts a DataFrame to a compact string for the LLM prompt.
    Uses the first max_rows rows to keep the prompt short.
    """
    if df is None or len(df) == 0:
        return "No data returned."

    # Use to_string for small results, CSV format for larger ones
    if len(df) <= max_rows:
        return df.to_string(index=False, max_colwidth=30)
    else:
        sample = df.head(max_rows)
        return (
            f"Showing first {max_rows} of {len(df)} rows:\n"
            + sample.to_string(index=False, max_colwidth=30)
        )


def _build_user_message(state: AgentState) -> str:
    """Build the message that asks Claude to generate a plain-English answer."""
    query        = state["user_query"]
    df           = state.get("query_result")
    error        = state.get("execution_error")
    row_count    = state.get("result_row_count", 0)
    exec_time_ms = state.get("execution_time_ms", 0)

    if error and not df:
        # Error case — ask for graceful failure message
        return f"""The user asked: "{query}"

The SQL query failed with error: {error}

Write a brief, helpful response explaining:
1. That you couldn't answer this specific question
2. What the user can ask instead (refer to certified metrics)

Keep it under 3 sentences. Be helpful, not apologetic."""

    # Success case
    data_str = _df_to_prompt_string(df)

    return f"""The user asked: "{query}"

Query returned {row_count} rows in {exec_time_ms:.0f}ms:
{data_str}

Write a direct, concise answer (2-3 sentences max):
1. Answer the question directly with the key number(s)
2. Add one brief contextual observation if the data supports it
3. Do NOT mention SQL, databases, or technical details

Return ONLY a JSON object:
{{
  "answer": "your 2-3 sentence plain-English answer here",
  "followup": "one specific follow-up question the user might want to ask next"
}}"""


def generate_response(state: AgentState) -> AgentState:
    """
    Node 5: Generate plain-English answer.
    Deterministic confidence scoring + Claude Sonnet for the natural language.
    Also handles rejection path — if final_answer already set by guardrail,
    returns immediately without making an API call.
    """
    # ── Early return: rejection path already set final_answer ─────────────────
    # When guardrail rejects or intent=out_of_scope, pipeline.py sets
    # final_answer directly and routes here just to hit END.
    # No LLM call needed — just return state as-is.
    if state.get("final_answer") and not state.get("query_result") and not state.get("generated_sql"):
        print(f"[Node 5] Returning pre-set rejection answer (no LLM call)")
        return {
            **state,
            "confidence": state.get("confidence", "low"),
            "suggested_followup": "",
        }

    client     = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    confidence = _score_confidence(state)

    # ── System prompt ─────────────────────────────────────────────────────────
    system_prompt = """You are an analytics assistant for a SaaS company.
You answer questions about business metrics clearly and directly.
Always lead with the number. Be specific. No fluff.
Return only valid JSON — no markdown, no preamble."""

    # ── API call ──────────────────────────────────────────────────────────────
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=300,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": _build_user_message(state)
            }]
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1].lstrip("json").strip()

        result = json.loads(raw_text)
        answer    = result.get("answer", "I was unable to generate an answer.")
        followup  = result.get("followup", "")

        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        print(f"[Node 5] ✅ Answer generated | Confidence: {confidence} | Tokens: {input_tokens}in/{output_tokens}out")

        return {
            **state,
            "final_answer":       answer,
            "confidence":         confidence,
            "suggested_followup": followup,
            "total_input_tokens":  state.get("total_input_tokens",  0) + input_tokens,
            "total_output_tokens": state.get("total_output_tokens", 0) + output_tokens,
        }

    except json.JSONDecodeError:
        # Fallback: use raw text as answer if JSON parsing fails
        raw = response.content[0].text if response else "Unable to generate answer."
        return {
            **state,
            "final_answer":       raw[:500],
            "confidence":         "low",
            "suggested_followup": "",
        }

    except Exception as e:
        print(f"[Node 5] Error: {e}")
        return {
            **state,
            "final_answer":       f"I encountered an error generating the answer: {e}",
            "confidence":         "low",
            "suggested_followup": "",
            "error_message":      str(e),
        }
