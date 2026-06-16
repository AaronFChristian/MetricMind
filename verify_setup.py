"""
verify_setup.py
===============
PURPOSE:
    Run this after `dbt seed` and `dbt run` to confirm that all 4 staging
    models and 4 mart models built correctly, and to print a quick summary
    of the data so you know it looks sensible.

HOW TO RUN:
    python verify_setup.py

WHAT IT DOES:
    1. Connects to your local DuckDB file
    2. Lists every table/view that dbt created
    3. Prints row counts for each model
    4. Shows a 3-row preview of each staging model
    5. Prints a summary of the "dirty data" rows that dbt tests caught
    6. Shows sample metric output from the mart models

INPUT:
    - data/metricmind.duckdb  (created by dbt seed + dbt run)
    - .env file with DUCKDB_PATH

OUTPUT:
    - Console printout of all tables, row counts, previews, and metric samples
    - If something is missing, it tells you exactly which dbt command to re-run

CONCEPT:
    DuckDB is like SQLite but built for analytics. dbt creates views for
    staging models (no data copied, just a SQL lens over the raw seeds) and
    tables for mart models (actual materialised data). This script queries
    both to verify the full pipeline worked end to end.
"""

import os
import sys
import duckdb
import pandas as pd
from dotenv import load_dotenv

# ── Load .env ────────────────────────────────────────────────────────────────
load_dotenv()

DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./data/metricmind.duckdb")

# Pandas display settings — show full column content in terminal
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", 120)
pd.set_option("display.max_colwidth", 30)

# ── Colours for terminal output ───────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"{GREEN}  ✅  {msg}{RESET}")
def fail(msg):  print(f"{RED}  ❌  {msg}{RESET}")
def warn(msg):  print(f"{YELLOW}  ⚠️   {msg}{RESET}")
def header(msg):print(f"\n{BOLD}{'─'*60}\n{msg}\n{'─'*60}{RESET}")

# ── Connect ───────────────────────────────────────────────────────────────────
def connect():
    """Open a read-only connection to the DuckDB file."""
    if not os.path.exists(DUCKDB_PATH):
        fail(f"DuckDB file not found at: {DUCKDB_PATH}")
        print("\nYou need to run dbt first. From inside dbt_project/:")
        print("  dbt seed --target duckdb")
        print("  dbt run  --target duckdb")
        sys.exit(1)
    return duckdb.connect(DUCKDB_PATH, read_only=True)

# ── Check 1: List all tables dbt created ─────────────────────────────────────
def check_tables(conn):
    header("STEP 1 — Tables & Views created by dbt")

    # DuckDB stores schemas, each schema contains tables/views
    tables = conn.execute("""
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
    """).fetchdf()

    if tables.empty:
        fail("No tables found! Run:  dbt seed && dbt run  inside dbt_project/")
        return False

    print(tables.to_string(index=False))

    # Expected objects
    expected = {
        # Seeds (raw tables in main schema)
        ("main",          "raw_users"):          "seed",
        ("main",          "raw_events"):         "seed",
        ("main",          "raw_subscriptions"):  "seed",
        ("main",          "raw_payments"):       "seed",
        # Staging (views in main_staging schema)
        ("main_staging",  "stg_users"):          "view",
        ("main_staging",  "stg_events"):         "view",
        ("main_staging",  "stg_subscriptions"):  "view",
        ("main_staging",  "stg_payments"):       "view",
    }

    all_ok = True
    print()
    for (schema, name), kind in expected.items():
        row = tables[(tables.table_schema == schema) & (tables.table_name == name)]
        if row.empty:
            fail(f"Missing: {schema}.{name}  →  run `dbt {'seed' if kind=='seed' else 'run'} --target duckdb`")
            all_ok = False
        else:
            ok(f"{schema}.{name}")

    return all_ok

# ── Check 2: Row counts ───────────────────────────────────────────────────────
def check_row_counts(conn):
    header("STEP 2 — Row counts (expected vs actual)")

    checks = [
        # (schema,         table,             min_expected, description)
        ("main",          "raw_users",         900,  "raw users (1000 total, ~3% may be null)"),
        ("main",          "raw_events",        7500, "raw events (8000 seeded)"),
        ("main",          "raw_subscriptions", 700,  "raw subscriptions"),
        ("main",          "raw_payments",      4500, "raw payments"),
        ("main_staging",  "stg_users",         900,  "cleaned users (views query raw table)"),
        ("main_staging",  "stg_events",        7500, "cleaned events"),
        ("main_staging",  "stg_subscriptions", 700,  "cleaned subscriptions"),
        ("main_staging",  "stg_payments",      4500, "cleaned payments"),
    ]

    for schema, table, min_rows, desc in checks:
        try:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {schema}.{table}"
            ).fetchone()[0]
            if count >= min_rows:
                ok(f"{schema}.{table}: {count:,} rows  ({desc})")
            else:
                warn(f"{schema}.{table}: only {count:,} rows (expected ≥{min_rows}) — {desc}")
        except Exception as e:
            fail(f"{schema}.{table}: {e}")

# ── Check 3: Staging model previews ───────────────────────────────────────────
def check_staging_previews(conn):
    header("STEP 3 — Staging model previews (3 rows each)")

    models = ["stg_users", "stg_events", "stg_subscriptions", "stg_payments"]
    for model in models:
        print(f"\n  {BOLD}main_staging.{model}{RESET}")
        try:
            df = conn.execute(
                f"SELECT * FROM main_staging.{model} LIMIT 3"
            ).fetchdf()
            print(df.to_string(index=False))
        except Exception as e:
            fail(f"Could not preview {model}: {e}")

# ── Check 4: Dirty data summary ───────────────────────────────────────────────
def check_dirty_data(conn):
    header("STEP 4 — Dirty data caught by dbt tests (this is the portfolio story)")

    checks = [
        (
            "NULL user_ids in raw_users",
            "SELECT COUNT(*) FROM main.raw_users WHERE user_id IS NULL",
            "These will FAIL the not_null test on stg_users — expected ~22 rows"
        ),
        (
            "Legacy event types (should not exist)",
            "SELECT COUNT(*) FROM main_staging.stg_events WHERE event_type NOT IN "
            "('page_view','feature_used','api_call','export','login','settings_change')",
            "Caught by accepted_values test — expected ~80 rows with 'legacy_click'"
        ),
        (
            "Payments with negative amount + no refund flag",
            "SELECT COUNT(*) FROM main_staging.stg_payments WHERE is_suspected_dirty = true",
            "Caught by test_payments_refund_consistency — expected ~128 rows"
        ),
        (
            "Subscriptions with negative MRR",
            "SELECT COUNT(*) FROM main_staging.stg_subscriptions WHERE mrr_usd < 0",
            "Caught by not_positive test — expected ~13 rows"
        ),
        (
            "Non-USD payments",
            "SELECT COUNT(*) FROM main_staging.stg_payments WHERE is_usd = false",
            "Schema inconsistency — expected ~24 rows with currency='EUR'"
        ),
    ]

    print(f"\n  {'Issue':<45} {'Count':>7}  {'Notes'}")
    print(f"  {'─'*44} {'─'*7}  {'─'*40}")
    for label, query, note in checks:
        try:
            count = conn.execute(query).fetchone()[0]
            marker = "⚠️ " if count > 0 else "✅"
            print(f"  {marker} {label:<43} {count:>7}  {note}")
        except Exception as e:
            print(f"  ❓ {label:<43} {'ERROR':>7}  {e}")

    print(f"\n  {YELLOW}These are INTENTIONAL dirty rows — dbt tests catch them.{RESET}")
    print(f"  {YELLOW}Interview answer: 'I designed the data to have known issues{RESET}")
    print(f"  {YELLOW}so I could demonstrate that dbt tests prevent bad data{RESET}")
    print(f"  {YELLOW}from reaching dashboards.'{RESET}")

# ── Check 5: is_activity_event flag (governance story) ────────────────────────
def check_activity_governance(conn):
    header("STEP 5 — Activity governance flag (the 'active user' definition)")

    df = conn.execute("""
        SELECT
            event_type,
            is_activity_event,
            COUNT(*) AS event_count
        FROM main_staging.stg_events
        GROUP BY 1, 2
        ORDER BY is_activity_event DESC, event_count DESC
    """).fetchdf()

    print(df.to_string(index=False))
    print(f"\n  {GREEN}is_activity_event=True rows count toward DAU/MAU.{RESET}")
    print(f"  {GREEN}This flag is set by vars.activity_event_types in dbt_project.yml.{RESET}")
    print(f"  {GREEN}Change the var → every metric updates automatically.{RESET}")

# ── Check 6: Mart model status ────────────────────────────────────────────────
def check_mart_models(conn):
    header("STEP 6 — Mart models (run `dbt run --select marts` if missing)")

    mart_queries = {
        "mart_user_metrics": (
            "main_marts", "mart_user_metrics",
            "SELECT metric_date, plan, country, daily_active_users FROM main_marts.mart_user_metrics LIMIT 3"
        ),
        "mart_cohort_retention": (
            "main_marts", "mart_cohort_retention",
            "SELECT cohort_month, retention_period, cohort_size, retained_users, retention_rate_pct FROM main_marts.mart_cohort_retention LIMIT 3"
        ),
        "mart_revenue_metrics": (
            "main_marts", "mart_revenue_metrics",
            "SELECT metric_month, plan, mrr, arr, churn_rate_pct FROM main_marts.mart_revenue_metrics LIMIT 3"
        ),
        "mart_funnel_analysis": (
            "main_marts", "mart_funnel_analysis",
            "SELECT cohort_month, plan, signups, activation_rate_pct, signup_to_paid_rate_pct, day30_retention_rate_pct FROM main_marts.mart_funnel_analysis LIMIT 3"
        ),
    }

    for name, (schema, table, query) in mart_queries.items():
        print(f"\n  {BOLD}{schema}.{name}{RESET}")
        try:
            df = conn.execute(query).fetchdf()
            if df.empty:
                warn(f"Table exists but has 0 rows — re-run: dbt run --select marts --target duckdb")
            else:
                print(df.to_string(index=False))
                ok(f"{len(df)} preview rows shown")
        except Exception as e:
            fail(f"Could not query {name}: {e}")
            print(f"       Run: dbt run --select marts --target duckdb")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{BOLD}{'='*60}")
    print("  MetricMind — Day 1 Setup Verification")
    print(f"{'='*60}{RESET}")
    print(f"  Database: {DUCKDB_PATH}")

    conn = connect()

    ok_tables = check_tables(conn)
    if not ok_tables:
        print(f"\n{RED}Stop here and run dbt first — see README.md Step 3{RESET}")
        return

    check_row_counts(conn)
    check_staging_previews(conn)
    check_dirty_data(conn)
    check_activity_governance(conn)
    check_mart_models(conn)

    conn.close()

    print(f"\n{BOLD}{'='*60}")
    print(f"  Day 1 verification complete.")
    print(f"  If staging models show ✅ and dirty data shows ⚠️ counts,")
    print(f"  everything is working exactly as designed.")
    print(f"{'='*60}{RESET}\n")

if __name__ == "__main__":
    main()
