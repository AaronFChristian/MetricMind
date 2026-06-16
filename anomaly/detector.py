"""
anomaly/detector.py
====================
PURPOSE:
    Detects anomalies in metric time series and generates
    plain-English commentary using Claude Sonnet.

CONCEPT: Dual-Method Detection
    We use two methods and flag when EITHER triggers:

    Method 1 — Statistical (3σ):
        Compute rolling mean and standard deviation over the past 30 days.
        If today's value is more than 3 standard deviations from the mean,
        it's anomalous. Fast, always works, easy to explain.

    Method 2 — Prophet (Facebook):
        Fits a trend + seasonality model to the historical data.
        Predicts an expected range (confidence interval) for each date.
        If the actual value falls outside the 95% CI, it's anomalous.
        Better for metrics with weekly seasonality (DAU always dips Sunday).

    Why both? Use 3σ as a quick sanity check, Prophet for nuanced detection.
    In an interview: "3σ catches sudden spikes, Prophet catches
    gradual trend breaks that 3σ misses because the rolling mean drifts."

HUMAN-IN-THE-LOOP:
    Every generated commentary is flagged human_review_required=True.
    The UI shows an "Approve" button before the commentary is published.
    This is your HITL checkpoint — shows AI maturity.

INPUT:
    DuckDB mart tables (mart_user_metrics, mart_revenue_metrics)

OUTPUT:
    List of AnomalyAlert dicts:
    {
        metric_name, date, actual_value, expected_value,
        deviation_pct, method, commentary, human_review_required
    }

USAGE:
    python anomaly/detector.py
    from anomaly.detector import detect_all_anomalies
"""

import os
import warnings
warnings.filterwarnings("ignore")  # suppress Prophet/Stan output

import duckdb
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
from anthropic import Anthropic
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
_SIGMA_THRESHOLD  = 3.0    # flag if value > 3σ from rolling mean
_PROPHET_CI       = 0.95   # Prophet confidence interval
_MIN_HISTORY_DAYS = 14     # need at least 14 days to detect anomalies
_MODEL            = "claude-haiku-4-5"  # Haiku for commentary — concise, cheap


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_dau_series(db_path: str) -> pd.DataFrame:
    """Load daily DAU time series aggregated across all dimensions."""
    conn = duckdb.connect(db_path, read_only=True)
    df = conn.execute("""
        SELECT
            metric_date     as ds,
            SUM(daily_active_users) as y
        FROM main_marts.mart_user_metrics
        GROUP BY metric_date
        ORDER BY metric_date
    """).fetchdf()
    conn.close()
    df["ds"] = pd.to_datetime(df["ds"])
    df["metric"] = "daily_active_users"
    return df


def _load_mrr_series(db_path: str) -> pd.DataFrame:
    """Load monthly MRR time series."""
    conn = duckdb.connect(db_path, read_only=True)
    df = conn.execute("""
        SELECT
            metric_month    as ds,
            SUM(mrr)        as y
        FROM main_marts.mart_revenue_metrics
        GROUP BY metric_month
        ORDER BY metric_month
    """).fetchdf()
    conn.close()
    df["ds"] = pd.to_datetime(df["ds"])
    df["metric"] = "monthly_recurring_revenue"
    return df


# ── Detection methods ──────────────────────────────────────────────────────────

def detect_sigma_anomalies(df: pd.DataFrame, window: int = 30) -> list[dict]:
    """
    3-sigma rolling window anomaly detection.
    Simple, fast, no external dependencies.
    """
    if len(df) < _MIN_HISTORY_DAYS:
        return []

    df = df.copy().sort_values("ds")
    df["rolling_mean"] = df["y"].rolling(window, min_periods=7).mean()
    df["rolling_std"]  = df["y"].rolling(window, min_periods=7).std()
    df["z_score"]      = (df["y"] - df["rolling_mean"]) / df["rolling_std"].replace(0, np.nan)

    anomalies = []
    for _, row in df[df["z_score"].abs() > _SIGMA_THRESHOLD].iterrows():
        if pd.isna(row["z_score"]):
            continue
        expected   = row["rolling_mean"]
        actual     = row["y"]
        deviation  = ((actual - expected) / expected * 100) if expected != 0 else 0
        anomalies.append({
            "metric_name":    df["metric"].iloc[0],
            "date":           row["ds"].strftime("%Y-%m-%d"),
            "actual_value":   round(float(actual), 2),
            "expected_value": round(float(expected), 2),
            "deviation_pct":  round(float(deviation), 1),
            "z_score":        round(float(row["z_score"]), 2),
            "method":         "3sigma",
            "direction":      "spike" if actual > expected else "drop",
        })

    return anomalies


def detect_prophet_anomalies(df: pd.DataFrame) -> list[dict]:
    """
    Prophet-based anomaly detection.
    Better for metrics with weekly seasonality (e.g. DAU drops on weekends).
    """
    if len(df) < _MIN_HISTORY_DAYS:
        return []

    try:
        from prophet import Prophet

        # Prophet requires columns named 'ds' and 'y' — already formatted
        model = Prophet(
            interval_width=_PROPHET_CI,
            daily_seasonality=False,
            weekly_seasonality=True,
            yearly_seasonality=True,
            changepoint_prior_scale=0.05,  # conservative trend changes
        )

        # Suppress Prophet's verbose Stan output
        import logging
        logging.getLogger("prophet").setLevel(logging.WARNING)
        logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

        model.fit(df[["ds", "y"]])
        forecast = model.predict(df[["ds"]])

        # Merge actual vs predicted
        merged = df.merge(
            forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]],
            on="ds"
        )

        anomalies = []
        for _, row in merged.iterrows():
            actual = row["y"]
            # Flag if outside the confidence interval
            if actual < row["yhat_lower"] or actual > row["yhat_upper"]:
                expected   = row["yhat"]
                deviation  = ((actual - expected) / expected * 100) if expected != 0 else 0
                anomalies.append({
                    "metric_name":    df["metric"].iloc[0],
                    "date":           row["ds"].strftime("%Y-%m-%d"),
                    "actual_value":   round(float(actual), 2),
                    "expected_value": round(float(expected), 2),
                    "expected_lower": round(float(row["yhat_lower"]), 2),
                    "expected_upper": round(float(row["yhat_upper"]), 2),
                    "deviation_pct":  round(float(deviation), 1),
                    "z_score":        None,
                    "method":         "prophet",
                    "direction":      "spike" if actual > expected else "drop",
                })

        return anomalies

    except ImportError:
        print("Prophet not installed — skipping Prophet detection. Run: pip install prophet")
        return []
    except Exception as e:
        print(f"Prophet detection error: {e}")
        return []


# ── LLM Commentary ────────────────────────────────────────────────────────────

def generate_commentary(anomaly: dict) -> str:
    """
    Generate a plain-English explanation of the anomaly using Claude Haiku.
    Short, specific, actionable. Human will review before publishing.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""An anomaly was detected in a SaaS metrics dashboard.

Metric: {anomaly['metric_name']}
Date: {anomaly['date']}
Actual value: {anomaly['actual_value']:,.0f}
Expected value: {anomaly['expected_value']:,.0f}
Deviation: {anomaly['deviation_pct']:+.1f}%
Direction: {anomaly['direction']}
Detection method: {anomaly['method']}

Write a 2-sentence plain-English commentary for a business audience:
1. State what happened (the number and percent change)
2. Suggest the most likely cause and recommended action

Be specific. No jargon. No "it appears that" hedging.
Return only the 2-sentence commentary — nothing else."""

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        return f"Anomaly detected: {anomaly['metric_name']} was {anomaly['deviation_pct']:+.1f}% from expected on {anomaly['date']}."


# ── Main detector ──────────────────────────────────────────────────────────────

def detect_all_anomalies(db_path: str, generate_llm_commentary: bool = True) -> list[dict]:
    """
    Run anomaly detection on all metrics.
    Returns list of anomaly dicts with commentary and HITL flag.
    """
    all_anomalies = []

    # Load all metric series
    series_loaders = [
        ("daily_active_users",        _load_dau_series),
        ("monthly_recurring_revenue", _load_mrr_series),
    ]

    for metric_name, loader in series_loaders:
        print(f"  Scanning {metric_name}...")
        try:
            df = loader(db_path)

            # Run both detection methods
            sigma_anomalies   = detect_sigma_anomalies(df)
            prophet_anomalies = detect_prophet_anomalies(df)

            # Combine and deduplicate by date (prefer Prophet if both flag same date)
            combined = {}
            for a in sigma_anomalies + prophet_anomalies:
                date = a["date"]
                # Prophet result overwrites sigma for same date (more sophisticated)
                if date not in combined or a["method"] == "prophet":
                    combined[date] = a

            print(f"    {len(combined)} anomalies found in {metric_name}")

            # Generate LLM commentary for each
            for date, anomaly in sorted(combined.items()):
                if generate_llm_commentary:
                    commentary = generate_commentary(anomaly)
                else:
                    commentary = f"Auto-flagged: {anomaly['deviation_pct']:+.1f}% deviation on {date}"

                anomaly["commentary"]             = commentary
                anomaly["human_review_required"]  = True  # always require human approval
                anomaly["commentary_approved"]    = False
                anomaly["detected_at"]            = datetime.now().isoformat()
                all_anomalies.append(anomaly)

        except Exception as e:
            print(f"    Error scanning {metric_name}: {e}")

    return all_anomalies


# ── Save anomalies to DuckDB ──────────────────────────────────────────────────

def save_anomalies(anomalies: list[dict], db_path: str) -> None:
    """Save detected anomalies to a DuckDB table for the UI to query."""
    if not anomalies:
        return

    conn = duckdb.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS main.anomaly_feed (
            id                   INTEGER,
            metric_name          VARCHAR,
            anomaly_date         VARCHAR,
            actual_value         DOUBLE,
            expected_value       DOUBLE,
            deviation_pct        DOUBLE,
            direction            VARCHAR,
            method               VARCHAR,
            commentary           VARCHAR,
            human_review_required BOOLEAN,
            commentary_approved  BOOLEAN,
            detected_at          TIMESTAMP
        )
    """)

    for i, a in enumerate(anomalies):
        conn.execute("""
            INSERT INTO main.anomaly_feed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            i, a["metric_name"], a["date"],
            a["actual_value"], a["expected_value"], a["deviation_pct"],
            a["direction"], a["method"], a["commentary"],
            a["human_review_required"], a["commentary_approved"],
            a["detected_at"]
        ])

    conn.close()
    print(f"  Saved {len(anomalies)} anomalies to main.anomaly_feed")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    db_path = os.environ.get("DUCKDB_PATH", "./data/metricmind.duckdb")
    print(f"\nRunning anomaly detection on {db_path}...\n")

    anomalies = detect_all_anomalies(db_path, generate_llm_commentary=True)

    print(f"\n{'='*60}")
    print(f"Found {len(anomalies)} anomalies total")
    print(f"{'='*60}")

    for a in anomalies[:5]:  # show first 5
        print(f"\n  [{a['metric_name']}] {a['date']}")
        print(f"  Actual: {a['actual_value']:,.0f} | Expected: {a['expected_value']:,.0f} | {a['deviation_pct']:+.1f}%")
        print(f"  Method: {a['method']}")
        print(f"  Commentary: {a['commentary']}")
        print(f"  ⚠️  Requires human review before publishing")

    if anomalies:
        save_anomalies(anomalies, db_path)
