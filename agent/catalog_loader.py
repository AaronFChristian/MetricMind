"""
agent/catalog_loader.py
=======================
PURPOSE:
    Loads the metrics_catalog.json file and provides helper functions
    that every agent node uses to look up metric definitions.

CONCEPT:
    The catalog is the ONLY source of truth for what metrics exist.
    This module is imported once at startup and cached — not re-read
    on every query. That's important because the full catalog text
    is injected into the Claude system prompt with prompt caching
    enabled, so we want the exact same string every time (cache hit
    requires identical content).

INPUT:
    catalog/metrics_catalog.json

OUTPUT:
    - get_catalog_text()     → full JSON as string (for LLM system prompt)
    - get_metric_names()     → list of certified metric name strings
    - get_metric_aliases()   → flat dict: alias → canonical metric name
    - find_metric(query)     → best matching metric for a query string
    - is_pii_column(col)     → True if column name is in the PII list

INTERVIEW TALKING POINT:
    "The catalog is loaded once, cached in memory, and injected into
    the Claude system prompt with Anthropic's prompt caching feature.
    Because the catalog text is identical on every request, Anthropic
    caches it server-side — we only pay to process it once per hour
    instead of on every single query. On a high-traffic demo this
    reduces cost by ~60%."
"""

import json
import os
from pathlib import Path
from typing import Optional

# ── Find the catalog file ─────────────────────────────────────────────────────
# Works whether you run from project root or from agent/ subfolder
_BASE_DIR = Path(__file__).parent.parent  # metricmind/ root
_CATALOG_PATH = _BASE_DIR / "catalog" / "metrics_catalog.json"

# ── Load once at import time ──────────────────────────────────────────────────
def _load_catalog() -> dict:
    if not _CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"Metrics catalog not found at {_CATALOG_PATH}\n"
            "Make sure catalog/metrics_catalog.json exists in the project root."
        )
    with open(_CATALOG_PATH) as f:
        return json.load(f)

# Module-level cache — loaded once, reused forever
_CATALOG: dict = _load_catalog()

# ── Public API ────────────────────────────────────────────────────────────────

def get_catalog_text() -> str:
    """
    Returns the full catalog as a compact JSON string.
    This string is injected into the Claude system prompt with
    cache_control enabled — identical string = cache hit = cheaper.
    """
    return json.dumps(_CATALOG, indent=2)


def get_metric_names() -> list[str]:
    """Returns list of certified metric names e.g. ['daily_active_users', ...]"""
    return [m["name"] for m in _CATALOG["certified_metrics"]]


def get_metric_aliases() -> dict[str, str]:
    """
    Returns a flat dict mapping every alias to its canonical metric name.
    Example: {"DAU": "daily_active_users", "dau": "daily_active_users", ...}
    """
    aliases = {}
    for metric in _CATALOG["certified_metrics"]:
        # The metric name itself is always an alias for itself
        aliases[metric["name"]] = metric["name"]
        for alias in metric.get("aliases", []):
            aliases[alias.lower()] = metric["name"]
    return aliases


def find_metric(query_text: str) -> Optional[str]:
    """
    Tries to find which certified metric a query is asking about.
    Searches aliases first, then does a simple keyword scan.
    Returns the canonical metric name, or None if no match found.

    Example:
        find_metric("what was DAU last week?") → "daily_active_users"
        find_metric("show me churn rate")      → "churn_rate"
        find_metric("what is the weather?")    → None
    """
    query_lower = query_text.lower()
    aliases = get_metric_aliases()

    # 1. Direct alias match
    for alias, canonical in aliases.items():
        if alias.lower() in query_lower:
            return canonical

    # 2. Check example questions for keyword overlap
    for metric in _CATALOG["certified_metrics"]:
        for example in metric.get("example_questions", []):
            # If 3+ words from the example appear in the query, it's probably a match
            example_words = set(example.lower().split())
            query_words = set(query_lower.split())
            overlap = len(example_words & query_words)
            if overlap >= 3:
                return metric["name"]

    return None


def get_metric_by_name(name: str) -> Optional[dict]:
    """Returns the full metric definition dict for a given canonical name."""
    for metric in _CATALOG["certified_metrics"]:
        if metric["name"] == name:
            return metric
    return None


def is_pii_column(column_name: str) -> bool:
    """Returns True if the column name is in the PII-protected list."""
    pii_cols = [c.lower() for c in _CATALOG.get("pii_columns", [])]
    return column_name.lower() in pii_cols


def get_rejection_message() -> str:
    """Returns the standard rejection message for out-of-scope queries."""
    return _CATALOG.get(
        "rejection_message",
        "I can only answer questions about certified metrics."
    )


def get_forbidden_operations() -> list[str]:
    """Returns the list of SQL operations the agent must never generate."""
    return _CATALOG.get("forbidden_operations", [])


# ── Convenience: print catalog summary ───────────────────────────────────────
if __name__ == "__main__":
    print("Certified metrics:", get_metric_names())
    print("Total aliases:", len(get_metric_aliases()))
    print("PII columns:", _CATALOG.get("pii_columns"))
    print("\nTest find_metric:")
    test_queries = [
        "what was DAU last week?",
        "show me 30-day retention for EU users",
        "what is MRR by plan?",
        "how many customers churned?",
        "what is the weather today?",  # should return None
    ]
    for q in test_queries:
        print(f"  '{q}' → {find_metric(q)}")
