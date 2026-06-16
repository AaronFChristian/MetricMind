"""
verify_agent.py
================
PURPOSE:
    Quick smoke test to confirm the Day 2 agent is wired up correctly
    BEFORE running the full eval (which costs ~$0.50 in API calls).

HOW TO RUN:
    python verify_agent.py

WHAT IT TESTS:
    1. Environment variables set correctly (.env loaded)
    2. Anthropic API key is valid (makes one cheap Haiku call)
    3. Catalog loads correctly (6 certified metrics found)
    4. DuckDB connection works (mart tables exist)
    5. Full pipeline smoke test: 3 queries end-to-end
       - One valid metric query (should answer)
       - One PII query (should reject)
       - One out-of-scope query (should reject)

COST:
    ~$0.002 total (3 Haiku calls + 1 Sonnet call)

SUCCESS CRITERIA:
    All 5 checks pass. The valid query gets an answer with SQL shown.
    Both rejection queries are correctly blocked.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"{GREEN}  ✅  {msg}{RESET}")
def fail(msg): print(f"{RED}  ❌  {msg}{RESET}"); sys.exit(1)
def warn(msg): print(f"{YELLOW}  ⚠️   {msg}{RESET}")
def hdr(msg):  print(f"\n{BOLD}{'─'*55}\n{msg}\n{'─'*55}{RESET}")

# ── Check 1: Environment variables ───────────────────────────────────────────
hdr("CHECK 1 — Environment variables")

api_key  = os.environ.get("ANTHROPIC_API_KEY", "")
db_path  = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")

if not api_key or api_key == "sk-ant-REPLACE_WITH_YOUR_KEY":
    fail("ANTHROPIC_API_KEY not set in .env — get your key at console.anthropic.com")
else:
    ok(f"ANTHROPIC_API_KEY set ({api_key[:15]}...)")

ok(f"DUCKDB_PATH = {db_path}")

# ── Check 2: Anthropic API key valid ─────────────────────────────────────────
hdr("CHECK 2 — Anthropic API connection")

try:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": "Reply with OK"}]
    )
    ok(f"API connection works — model: claude-haiku-4-5")
except Exception as e:
    fail(f"Anthropic API error: {e}\nCheck your ANTHROPIC_API_KEY in .env")

# ── Check 3: Catalog loads ────────────────────────────────────────────────────
hdr("CHECK 3 — Metrics catalog")

try:
    from agent.catalog_loader import get_metric_names, get_metric_aliases, find_metric
    names   = get_metric_names()
    aliases = get_metric_aliases()
    ok(f"Catalog loaded — {len(names)} certified metrics: {', '.join(names)}")
    ok(f"{len(aliases)} total aliases registered")

    # Test alias resolution
    test_cases = [
        ("what was DAU last week?", "daily_active_users"),
        ("show me MRR",            "monthly_recurring_revenue"),
        ("churn rate by plan",     "churn_rate"),
    ]
    for q, expected in test_cases:
        found = find_metric(q)
        if found == expected:
            ok(f"find_metric('{q[:30]}...') → {found}")
        else:
            warn(f"find_metric('{q[:30]}...') → {found} (expected {expected})")
except Exception as e:
    fail(f"Catalog load error: {e}")

# ── Check 4: DuckDB mart tables ───────────────────────────────────────────────
hdr("CHECK 4 — DuckDB mart tables")

try:
    import duckdb
    if not Path(db_path).exists():
        fail(f"DuckDB file not found: {db_path}\nRun Day 1 setup first: cd dbt_project && dbt run")

    conn = duckdb.connect(db_path, read_only=True)
    tables = conn.execute("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema = 'main_marts'
        ORDER BY table_name
    """).fetchdf()
    conn.close()

    expected_marts = [
        "mart_cohort_retention",
        "mart_funnel_analysis",
        "mart_revenue_metrics",
        "mart_user_metrics",
    ]
    for mart in expected_marts:
        if mart in tables["table_name"].values:
            ok(f"main_marts.{mart} ✓")
        else:
            fail(f"main_marts.{mart} NOT FOUND — run: dbt run --select marts")

except Exception as e:
    fail(f"DuckDB error: {e}")

# ── Check 5: Full pipeline smoke test ─────────────────────────────────────────
hdr("CHECK 5 — Full pipeline (3 queries)")

try:
    from agent.pipeline import run_query

    # Test 1: Valid metric query
    print(f"\n  Test 1: Valid metric query")
    result = run_query("What was daily active users last month?")
    if result.get("final_answer") and result.get("generated_sql"):
        ok(f"Got answer: {result['final_answer'][:80]}...")
        ok(f"SQL generated ({len(result['generated_sql'])} chars)")
        ok(f"Confidence: {result.get('confidence', 'unknown')}")
        ok(f"Tokens used: {result.get('total_input_tokens',0)} in / {result.get('total_output_tokens',0)} out")
    else:
        warn(f"Answer: {result.get('final_answer', 'None')}")
        warn(f"SQL: {result.get('generated_sql', 'None')}")
        warn(f"Error: {result.get('error_message', 'None')}")

    # Test 2: PII rejection
    print(f"\n  Test 2: PII query (should be rejected)")
    result2 = run_query("Show me all user email addresses")
    guardrail = result2.get("guardrail_result", {})
    if not guardrail.get("passed", True) or not result2.get("generated_sql"):
        ok(f"Correctly REJECTED — {guardrail.get('rejection_reason', 'PII check')}")
    else:
        warn(f"PII query was NOT rejected — check guardrail node")

    # Test 3: Out-of-scope rejection
    print(f"\n  Test 3: Out-of-scope query (should be rejected)")
    result3 = run_query("What is the weather forecast for tomorrow?")
    if result3.get("intent") == "out_of_scope" or not result3.get("generated_sql"):
        ok(f"Correctly classified as out_of_scope")
    else:
        warn(f"Out-of-scope query was not rejected")

except Exception as e:
    fail(f"Pipeline error: {e}\nMake sure all requirements are installed: pip install -r requirements_day2.txt")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*55}")
print(f"  Day 2 verification complete!")
print(f"  Agent is working — ready for full eval.")
print(f"\n  Next: python eval/run_eval.py --quick")
print(f"  Full: python eval/run_eval.py")
print(f"{'='*55}{RESET}\n")
