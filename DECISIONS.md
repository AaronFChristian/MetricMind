# MetricMind — Architectural Decision Records

> One-paragraph explanations of why each major technical choice was made.
> Written for engineers and hiring managers reviewing this codebase.

---

## ADR-001: Semantic Layer Over Raw Text-to-SQL

**Decision:** Constrain the LLM to query only certified mart models via a metrics catalog, rather than letting it write SQL against raw tables.

**Why:** Raw text-to-SQL allows the LLM to query any table and invent any column name. The result is unauditable — you can't tell if "active users" means DAU or MAU or something else. By routing all queries through dbt marts with a fixed schema, every answer is traceable to a documented metric definition. When the definition changes, one YAML update propagates everywhere automatically.

**Tradeoff:** Reduced flexibility — the agent can't answer questions outside the 6 certified metrics. This is a feature, not a bug: "I can't answer that from governed data" is a more trustworthy response than a hallucinated number.

---

## ADR-002: LangGraph Over a Single Chain

**Decision:** Use a 5-node LangGraph StateGraph instead of a single LangChain chain.

**Why:** A single chain can't route differently based on intermediate results. LangGraph gives us: (1) early termination for out-of-scope queries before expensive Sonnet calls, (2) conditional retry routing when SQL fails, (3) a dedicated rejection node that never calls the LLM. The pipeline cost for a rejected query is $0.0003 (Haiku only) vs $0.006 (full pipeline) — 20x cheaper for the common bad-query case.

**Tradeoff:** More code to maintain than a simple chain. Worth it at any production scale.

---

## ADR-003: sqlglot for SQL Comparison in Eval

**Decision:** Use sqlglot AST comparison in the eval harness instead of string matching.

**Why:** `SELECT a, b FROM t` and `select b,a from t` are semantically identical but string-different. The eval harness would report false failures if we used string matching, making it useless for detecting real regressions. sqlglot parses both into an AST and compares structure — whitespace, column order, and casing differences are ignored.

**Tradeoff:** Adds a dependency and ~50ms per eval question. Acceptable for a test harness that runs on PRs, not on every query.

---

## ADR-004: Claude Haiku for Guardrails, Sonnet for Generation

**Decision:** Use Claude Haiku for Node 1 (classify) and Node 2 (guardrail), Sonnet for Node 3 (SQL gen) and Node 5 (response).

**Why:** Classification and security checks are simple pattern-matching tasks — Haiku handles them correctly at ~20x lower cost than Sonnet. SQL generation requires stronger reasoning and knowledge of DuckDB syntax — Sonnet's quality improvement is worth the cost difference here. This blended model strategy reduces average cost per query from ~$0.012 (all Sonnet) to ~$0.006.

**Tradeoff:** Two models to maintain and monitor. LangSmith traces show per-node costs, making this easy to audit.

---

## ADR-005: Anthropic Prompt Caching on the Metric Catalog

**Decision:** Inject the full metrics catalog (~3,000 tokens) as a cached system prompt block using Anthropic's `cache_control: ephemeral` feature.

**Why:** The catalog is identical on every request. Without caching, every Node 3 call pays to process 3,000 tokens. With caching, the first call in a 5-minute window pays full price; subsequent calls pay 10% (cache read tokens are 90% cheaper). On a demo with 20 queries in 5 minutes, this saves ~$0.06 in catalog token costs alone. At production scale (thousands of queries/hour) this is a meaningful saving.

**Tradeoff:** Cache is scoped to 5 minutes per Anthropic's current policy. Long idle periods reset the cache. Acceptable given typical usage patterns.

---

## ADR-006: Estimated CAC via Fixed Channel Assumptions

**Decision:** Estimate CAC using fixed cost-per-channel assumptions rather than connecting to real ad spend data.

**Why:** Real CAC requires Google Ads + Facebook Ads integration via Fivetran, which is out of scope for a portfolio project. Rather than omitting the metric entirely or fabricating data, we use documented assumptions (paid_search=$85/customer, referral=$25, etc.) and clearly label the column `estimated_cac`. The DECISIONS.md and column description both acknowledge the limitation.

**Tradeoff:** Estimated CAC is not production-accurate. The architecture is correct — swapping in real ad spend data requires only updating the CTE in `mart_revenue_metrics.sql`.

---

## ADR-007: Prophet + 3σ Dual-Method Anomaly Detection

**Decision:** Run both 3-sigma rolling window and Facebook Prophet for anomaly detection, preferring Prophet when both flag the same date.

**Why:** 3σ is fast, simple, and easy to explain — it catches sudden spikes and drops immediately. Prophet models trend + weekly seasonality — it knows that DAU always dips on Sundays and won't flag that as an anomaly. For a metric with weekly patterns (most SaaS metrics), Prophet's false-positive rate is significantly lower. Running both and preferring Prophet gives us the speed of 3σ with the accuracy of Prophet.

**Tradeoff:** Prophet requires Stan (C++ compiler) and takes 5+ minutes to install. We document this clearly and fall back gracefully to 3σ if Prophet is unavailable.

---

## ADR-008: Human-in-the-Loop on Anomaly Commentary

**Decision:** All LLM-generated anomaly commentary is flagged `human_review_required=True` and requires explicit approval before publishing.

**Why:** LLMs are confidently wrong. An auto-published commentary saying "the DAU drop was caused by the pricing page launch" could be completely incorrect, mislead a business decision, and damage trust in the system. The HITL checkpoint adds one click of friction in exchange for human accountability over every published insight. This is the "AI-augmented, not AI-automated" philosophy that mature AI teams operate with.

**Tradeoff:** Requires a human in the loop. For a demo this is a feature — it shows architectural maturity. For production, the approval step can be automated once confidence thresholds are validated over time.
