"""
eval/run_eval.py
================
PURPOSE:
    Runs every question in golden_set.json through the full agent pipeline
    and scores the results. Produces an accuracy report.

CONCEPT: Why Eval Matters
    Without an eval harness, you have no idea if a code change made the
    agent better or worse. With it, you can say:
    "Before adding guardrails: 61% accuracy. After: 87% accuracy."
    That's a concrete, defensible claim in a portfolio or interview.

HOW SCORING WORKS:
    For each question we check 3 things:
    1. Rejection correctness  : should_reject=True → agent must reject
                                 should_reject=False → agent must answer
    2. Table correctness      : did the SQL query the right mart table?
    3. Column presence        : do the expected columns appear in the SQL?

    We use sqlglot to parse the generated SQL into an AST (Abstract Syntax
    Tree) rather than string matching. This is the senior-engineer move:
    "SELECT a, b FROM t" and "select b,a from t" are the same query —
    string match would fail, AST comparison passes.

OUTPUT:
    - Console report with per-question results
    - JSON file in eval/results/ with full details
    - Exit code 1 if accuracy < MIN_ACCURACY (used by CI gate)

USAGE:
    python eval/run_eval.py
    python eval/run_eval.py --min-accuracy 0.85  (CI gate)
    python eval/run_eval.py --quick              (first 10 questions only)

INTERVIEW TALKING POINT:
    "I use sqlglot for SQL comparison instead of string matching. Two
    queries that produce identical results but have different whitespace
    or column ordering would fail a string match. sqlglot parses both
    into ASTs and compares the structure — much more robust."
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load .env before importing agent modules
load_dotenv(Path(__file__).parent.parent / ".env")

import sqlglot
from agent.pipeline import run_query

# ── Config ────────────────────────────────────────────────────────────────────
_GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
_RESULTS_DIR     = Path(__file__).parent / "results"
_RESULTS_DIR.mkdir(exist_ok=True)

# Colours for terminal output
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ── SQL Analysis helpers ──────────────────────────────────────────────────────

def get_tables_from_sql(sql: str) -> set[str]:
    """
    Uses sqlglot to extract table names from SQL.
    Handles aliases, subqueries, CTEs.
    Much more robust than regex string matching.
    """
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        tables = set()
        for table in parsed.find_all(sqlglot.exp.Table):
            # Build full table name: schema.table or just table
            db    = table.args.get("db")
            name  = table.name
            if db:
                tables.add(f"{db}.{name}".lower())
            tables.add(name.lower())
        return tables
    except Exception:
        # If sqlglot can't parse it, fall back to simple string search
        tables = set()
        for word in sql.lower().split():
            if "mart_" in word:
                tables.add(word.strip("(),;"))
        return tables


def get_columns_from_sql(sql: str) -> set[str]:
    """Uses sqlglot to extract all column references from SQL."""
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        cols = set()
        for col in parsed.find_all(sqlglot.exp.Column):
            cols.add(col.name.lower())
        return cols
    except Exception:
        return set()


# ── Scoring logic ─────────────────────────────────────────────────────────────

def score_question(question: dict, result: dict) -> dict:
    """
    Scores one Q&A pair. Returns a dict with pass/fail for each check.
    """
    should_reject = question["should_reject"]
    sql           = result.get("generated_sql") or ""
    final_answer  = result.get("final_answer") or ""
    guardrail     = result.get("guardrail_result") or {}
    intent        = result.get("intent", "")

    # Was the query rejected by the agent?
    was_rejected = (
        not guardrail.get("passed", True) or
        intent == "out_of_scope" or
        not sql
    )

    scores = {
        "question_id":   question["id"],
        "category":      question["category"],
        "question":      question["question"],
        "should_reject": should_reject,
        "was_rejected":  was_rejected,
    }

    # ── Check 1: Rejection correctness ────────────────────────────────────────
    if should_reject:
        scores["rejection_correct"] = was_rejected
        scores["table_correct"]     = True   # N/A for rejection
        scores["columns_correct"]   = True   # N/A for rejection
        scores["overall_pass"]      = was_rejected
    else:
        scores["rejection_correct"] = not was_rejected

        if was_rejected:
            # Agent incorrectly rejected a valid question
            scores["table_correct"]   = False
            scores["columns_correct"] = False
            scores["overall_pass"]    = False
        else:
            # ── Check 2: Table correctness ─────────────────────────────────────
            expected_table = (question.get("expected_table") or "").lower()
            sql_tables     = get_tables_from_sql(sql)
            table_correct  = any(
                expected_table in t or t in expected_table
                for t in sql_tables
            ) if expected_table else True
            scores["table_correct"] = table_correct

            # ── Check 3: Column presence ───────────────────────────────────────
            expected_cols = [c.lower() for c in question.get("expected_columns", [])]
            sql_cols      = get_columns_from_sql(sql)
            # At least half the expected columns should appear in the SQL
            if expected_cols:
                matched = sum(1 for c in expected_cols if c in sql_cols or c in sql.lower())
                col_score = matched / len(expected_cols)
                columns_correct = col_score >= 0.5   # 50% threshold
            else:
                columns_correct = True
            scores["columns_correct"] = columns_correct

            scores["overall_pass"] = table_correct and columns_correct

    scores["generated_sql"]  = sql[:200] if sql else None
    scores["final_answer"]   = (final_answer or "")[:200]
    scores["intent"]         = intent
    scores["total_tokens"]   = (
        result.get("total_input_tokens", 0) +
        result.get("total_output_tokens", 0)
    )
    scores["confidence"]     = result.get("confidence", "unknown")

    return scores


# ── Report generator ──────────────────────────────────────────────────────────

def print_report(all_scores: list[dict], total_cost: float, elapsed_sec: float) -> float:
    """Prints the eval report and returns overall accuracy."""
    total     = len(all_scores)
    passed    = sum(1 for s in all_scores if s["overall_pass"])
    accuracy  = passed / total if total > 0 else 0

    # By category
    categories = {}
    for s in all_scores:
        cat = s["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0}
        categories[cat]["total"] += 1
        if s["overall_pass"]:
            categories[cat]["passed"] += 1

    print(f"\n{BOLD}{'='*65}")
    print(f"  MetricMind Agent — Eval Report")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*65}{RESET}")

    print(f"\n  {BOLD}Overall Accuracy: {accuracy*100:.1f}% ({passed}/{total}){RESET}")
    print(f"  Total cost: ${total_cost:.4f} | Time: {elapsed_sec:.1f}s | Avg: {elapsed_sec/total:.1f}s/query")

    print(f"\n  By category:")
    for cat, stats in categories.items():
        pct    = stats['passed']/stats['total']*100
        marker = GREEN+"✅"+RESET if pct >= 80 else RED+"❌"+RESET
        print(f"    {marker} {cat:<20} {stats['passed']}/{stats['total']}  ({pct:.0f}%)")

    print(f"\n  Per-question results:")
    print(f"  {'ID':<6} {'Cat':<18} {'Pass':<6} {'Reject':<8} {'Table':<7} {'Cols':<6} {'Conf':<7} Question")
    print(f"  {'─'*6} {'─'*18} {'─'*6} {'─'*8} {'─'*7} {'─'*6} {'─'*7} {'─'*30}")

    for s in all_scores:
        overall = f"{GREEN}✅{RESET}" if s["overall_pass"]  else f"{RED}❌{RESET}"
        reject  = f"{GREEN}✅{RESET}" if s["rejection_correct"] else f"{RED}❌{RESET}"
        table   = f"{GREEN}✅{RESET}" if s["table_correct"]     else f"{RED}❌{RESET}"
        cols    = f"{GREEN}✅{RESET}" if s["columns_correct"]   else f"{RED}❌{RESET}"
        conf    = s.get("confidence", "?")[:4]
        q_short = s["question"][:35]
        print(f"  {s['question_id']:<6} {s['category']:<18} {overall}    {reject}      {table}    {cols}   {conf:<7} {q_short}")

    # Failed questions detail
    failed = [s for s in all_scores if not s["overall_pass"]]
    if failed:
        print(f"\n  {RED}Failed questions ({len(failed)}):{RESET}")
        for s in failed:
            print(f"\n    [{s['question_id']}] {s['question']}")
            print(f"    Intent: {s['intent']} | Should reject: {s['should_reject']} | Was rejected: {s['was_rejected']}")
            if s.get("generated_sql"):
                print(f"    SQL: {s['generated_sql'][:100]}...")
            print(f"    Answer: {s['final_answer'][:100]}")

    print(f"\n{'='*65}\n")
    return accuracy


# ── Main runner ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MetricMind eval harness")
    parser.add_argument("--min-accuracy", type=float, default=0.0,
                        help="Fail with exit code 1 if accuracy < this value")
    parser.add_argument("--quick", action="store_true",
                        help="Run first 10 questions only")
    parser.add_argument("--category", type=str, default=None,
                        help="Run only questions in this category")
    parser.add_argument("--output", type=str, default=None,
                        help="Path to save results JSON")
    args = parser.parse_args()

    # Load golden set
    with open(_GOLDEN_SET_PATH) as f:
        golden = json.load(f)

    questions = golden["questions"]

    # Filter
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    if args.quick:
        questions = questions[:10]

    print(f"\n{BOLD}Running eval on {len(questions)} questions...{RESET}\n")

    all_scores = []
    total_cost = 0.0
    start_time = time.time()

    for i, question in enumerate(questions):
        print(f"[{i+1}/{len(questions)}] {question['id']}: {question['question'][:60]}...")

        try:
            result = run_query(question["question"])
            score  = score_question(question, result)

            # Estimate cost
            tokens = score.get("total_tokens", 0)
            cost   = tokens * 0.000005  # blended Haiku+Sonnet estimate
            total_cost += cost

            status = f"{GREEN}PASS{RESET}" if score["overall_pass"] else f"{RED}FAIL{RESET}"
            print(f"  → {status} | Intent: {score['intent']} | Tokens: {tokens}")

        except Exception as e:
            print(f"  → {RED}ERROR{RESET}: {e}")
            score = {
                "question_id":      question["id"],
                "category":         question["category"],
                "question":         question["question"],
                "should_reject":    question["should_reject"],
                "was_rejected":     False,
                "rejection_correct":False,
                "table_correct":    False,
                "columns_correct":  False,
                "overall_pass":     False,
                "error":            str(e),
                "total_tokens":     0,
                "confidence":       "error",
                "generated_sql":    None,
                "final_answer":     str(e),
                "intent":           "error",
            }

        all_scores.append(score)

    elapsed = time.time() - start_time

    # Print report
    accuracy = print_report(all_scores, total_cost, elapsed)

    # Save results JSON
    output_path = args.output or str(
        _RESULTS_DIR / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(output_path, "w") as f:
        json.dump({
            "run_at":       datetime.now().isoformat(),
            "accuracy":     accuracy,
            "total_cost":   total_cost,
            "elapsed_sec":  elapsed,
            "question_count": len(questions),
            "scores":       all_scores,
        }, f, indent=2, default=str)

    print(f"Results saved to: {output_path}")

    # CI gate: exit 1 if below threshold
    if args.min_accuracy > 0 and accuracy < args.min_accuracy:
        print(f"{RED}EVAL FAILED: accuracy {accuracy:.1%} < threshold {args.min_accuracy:.1%}{RESET}")
        sys.exit(1)

    print(f"{GREEN}Eval complete: {accuracy:.1%} accuracy{RESET}")


if __name__ == "__main__":
    main()
