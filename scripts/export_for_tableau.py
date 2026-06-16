"""
scripts/export_for_tableau.py
==============================
PURPOSE:
    Exports mart model data to CSV files for Tableau Public.
    Run this once, then connect Tableau Desktop/Public to the CSVs.

HOW TO RUN:
    python scripts/export_for_tableau.py

OUTPUT FILES (in tableau_exports/):
    mrr_trend.csv          - Monthly MRR by plan for the time-series chart
    cohort_retention.csv   - Cohort retention heatmap data
    funnel_analysis.csv    - Signup to retention funnel rates
    dau_trend.csv          - Daily active users over time

TABLEAU SETUP:
    1. Download Tableau Public (free): https://public.tableau.com/en-us/s/download
    2. Open Tableau Public
    3. Connect to Text File -> select any CSV from tableau_exports/
    4. Build Sheet 1: MRR trend line chart (metric_month on x, mrr on y, color by plan)
    5. Build Sheet 2: Retention heatmap (cohort_month on rows, retention_period on cols, retention_rate_pct on color)
    6. Combine into a Dashboard
    7. Publish to Tableau Public -> copy the embed URL
    8. Add the URL to your README
"""

import os
import duckdb
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DB_PATH    = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")
OUTPUT_DIR = Path("tableau_exports")
OUTPUT_DIR.mkdir(exist_ok=True)

def export(conn, query: str, filename: str, description: str):
    print(f"Exporting {filename}...", end=" ")
    df = conn.execute(query).fetchdf()
    path = OUTPUT_DIR / filename
    df.to_csv(path, index=False)
    print(f"{len(df)} rows -> {path}  ({description})")
    return df

conn = duckdb.connect(DB_PATH, read_only=True)

# ── 1. MRR trend by plan ──────────────────────────────────────────────────────
export(conn, """
    SELECT
        metric_month,
        plan,
        SUM(mrr)                as mrr,
        SUM(arr)                as arr,
        SUM(paying_customers)   as paying_customers,
        AVG(churn_rate_pct)     as churn_rate_pct,
        AVG(estimated_ltv)      as estimated_ltv,
        AVG(ltv_to_cac_ratio)   as ltv_to_cac_ratio
    FROM main_marts.mart_revenue_metrics
    WHERE mrr > 0
    GROUP BY metric_month, plan
    ORDER BY metric_month, plan
""", "mrr_trend.csv", "Sheet 1: MRR time-series line chart")

# ── 2. Cohort retention heatmap ───────────────────────────────────────────────
export(conn, """
    SELECT
        cohort_month,
        retention_period,
        plan,
        country,
        cohort_size,
        retained_users,
        retention_rate_pct
    FROM main_marts.mart_cohort_retention
    WHERE retention_period <= 12
    ORDER BY cohort_month DESC, retention_period ASC
""", "cohort_retention.csv", "Sheet 2: Retention heatmap")

# ── 3. Funnel analysis ────────────────────────────────────────────────────────
export(conn, """
    SELECT
        cohort_month,
        plan,
        country,
        acquisition_channel,
        signups,
        activations,
        conversions,
        day30_retained,
        activation_rate_pct,
        signup_to_paid_rate_pct,
        day30_retention_rate_pct
    FROM main_marts.mart_funnel_analysis
    WHERE signups > 0
    ORDER BY cohort_month DESC
""", "funnel_analysis.csv", "Sheet 3: Funnel conversion rates")

# ── 4. DAU trend ──────────────────────────────────────────────────────────────
export(conn, """
    SELECT
        metric_date,
        plan,
        country,
        acquisition_channel,
        SUM(daily_active_users) as dau,
        SUM(new_user_activations) as new_activations
    FROM main_marts.mart_user_metrics
    GROUP BY metric_date, plan, country, acquisition_channel
    ORDER BY metric_date DESC
""", "dau_trend.csv", "Sheet 4: DAU time-series")

conn.close()

print(f"\nAll exports saved to {OUTPUT_DIR}/")
print("\nTableau setup:")
print("  1. Download Tableau Public: https://public.tableau.com/en-us/s/download")
print("  2. Connect to Text File -> select mrr_trend.csv")
print("  3. Build Sheet 1: drag metric_month to Columns, mrr to Rows, plan to Color")
print("  4. Build Sheet 2: drag cohort_month to Rows, retention_period to Columns, retention_rate_pct to Color")
print("  5. Create Dashboard -> drag both sheets in")
print("  6. Publish to Tableau Public -> copy the URL -> add to README")
