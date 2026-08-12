# AI Agent Evals

Notes from Anthropic's ["Demystifying evals for AI agents"](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

## Core terminology

| Term | Meaning |
|---|---|
| Task / Problem / Test case | A single test with defined inputs and success criteria |
| Trial | One attempt at a task (run multiple trials since model output varies) |
| Grader | Scoring logic that assesses performance; a task can have several graders |
| Transcript / Trace / Trajectory | Full record of a trial — outputs, tool calls, reasoning, intermediate results |
| Outcome | Final state of the environment after a trial (e.g., does the DB row exist) |
| Evaluation harness | Infrastructure that runs trials concurrently, records steps, grades, and aggregates results |
| Agent harness / Scaffold | The system that lets the model act as an agent (handles input + tool orchestration) |
| Evaluation suite | A collection of related tasks measuring a specific capability or behavior |

## Types of evals

### By scope

- **Single-turn** — one prompt, one response, simple grading. The classic LLM-eval shape.
- **Multi-turn** — the model calls tools, changes state, and adapts across several steps.
- **Agent evals** — the hardest case: tool use across many turns, state mutation, and possible error propagation.

### By grader type

**Code-based graders**
- Methods: exact/regex/fuzzy string matching, unit tests, static analysis, outcome verification, tool-call verification, transcript analysis
- Strengths: fast, cheap, objective, reproducible, debuggable
- Weaknesses: brittle to valid variations, no nuance, poor fit for subjective tasks

**Model-based graders**
- Methods: rubric scoring, natural-language assertions, pairwise comparison, reference-based grading, multi-judge consensus
- Strengths: flexible, scalable, captures nuance, handles open-ended tasks
- Weaknesses: non-deterministic, costly, needs human calibration

**Human graders**
- Methods: SME review, crowdsourced judgment, spot checks, A/B testing, inter-annotator agreement
- Strengths: gold-standard quality, matches expert judgment, used to calibrate model-based graders
- Weaknesses: expensive, slow, requires expert access

### By purpose

- **Capability evals** — "What can this agent do well?" Start at low pass rates; target the hardest tasks; measure headroom for improvement.
- **Regression evals** — "Does the agent still do what it used to?" Should stay near 100% pass; a drop signals a real regression to investigate.
- Saturated capability evals (agent passes nearly everything) should **graduate into the regression suite**.

## When to use each type

| Agent type | Main challenge | What to grade with |
|---|---|---|
| **Coding agents** | Deterministic grading is natural here | Unit tests, transcript analysis for quality, static analysis (lint/type/security), state verification, tool-call checks. Benchmarks: SWE-bench Verified, Terminal-Bench |
| **Conversational agents** (support/sales/coaching) | The *quality of the interaction* is itself part of what's graded | LLM rubrics for empathy/clarity/policy-grounding, state checks (ticket resolved? refund issued?), tool-call sequence/parameter checks, turn limits. Often needs a second LLM to simulate the user. Benchmarks: τ-Bench, τ²-Bench |
| **Research agents** | "Comprehensive," "well-sourced," and "correct" are context-dependent | Combine graders: groundedness checks, coverage checks, source-quality checks, exact match for objective facts, LLM judgment for coherence/completeness. Calibrate LLM rubrics against human experts often. Benchmark: BrowseComp |
| **Computer-use agents** | Interacts via screenshots/clicks/keyboard, not APIs | Run in a real/sandboxed environment and check outcome: URL/page state, backend state, file system, app config, DB contents, UI element properties. Trade off DOM-based (fast, token-heavy) vs. screenshot-based (slower, token-efficient) interaction. Benchmarks: WebArena, OSWorld |

General rule: **use code-based graders wherever possible** (cheap, deterministic), fall back to **model-based graders** for nuance/open-endedness, and reserve **human graders** for calibration and high-stakes validation.

## Non-determinism metrics

- **pass@k** — probability at least one of k attempts succeeds; rises as k grows. E.g. "50% pass@1" = succeeds on the first try half the time. Use when *one* success is enough.
- **pass^k** — probability *all* k trials succeed; falls as k grows. E.g. 75% per-trial success → passing all 3 trials ≈ 42%. Use when consistency/reliability matters (customer-facing agents).

## Scoring approaches

- **Weighted** — combined grader scores must clear a threshold
- **Binary** — all graders must pass
- **Hybrid** — a mix of the two

## The starter 8 steps for building evals

**Step 0 — Start early.** Begin with 20–50 simple tasks sourced from real failures rather than waiting to accumulate hundreds. Early on, changes have an obvious effect, so small sample sizes are fine.

**Step 1 — Start with manual testing.** Turn existing manual checks, release-verification steps, and user-reported failures into test cases. Prioritize by user impact for the best ROI.

**Step 2 — Write unambiguous tasks with reference solutions.** Two domain experts should independently reach the same pass/fail verdict. Every task needs a known-working reference solution that passes all its graders, so agents don't fail on ambiguous specs through no fault of their own.

**Step 3 — Build balanced problem sets.** Cover both positive cases (behavior should happen) and negative cases (behavior should *not* happen). "One-sided evals create one-sided optimization" — avoid class imbalance.

**Step 4 — Build a robust eval harness with a stable environment.** The agent should behave like it does in production. Isolate every trial with a clean environment to avoid shared state causing correlated failures or inflated scores.

**Step 5 — Design graders thoughtfully.** Prefer deterministic graders, use LLM graders when needed, save human graders for validation. Grade *outcomes*, not exact step sequences — agents often find valid paths designers didn't anticipate. Add partial credit for multi-component tasks.

**Step 6 — Check the transcripts.** Actually read them to confirm graders work correctly. Failures should "seem fair" — it should be obvious what the agent got wrong and why. This tells you whether a low score is the agent's fault or the eval's.

**Step 7 — Monitor for eval saturation.** When an agent passes everything solvable, the eval stops providing signal. Recognize this and graduate the suite into a regression test. Note: a 0% pass rate across many trials is usually a sign of a **broken task**, not an incapable agent.

**Step 8 — Keep the eval suite healthy via open contribution and maintenance.** Have a dedicated infra team own the core system while domain experts contribute tasks. Treat maintaining evals as routine as maintaining unit tests.

## Guardrails / common pitfalls

- **Don't grade rigid step sequences** (e.g., exact tool-call order) — it over-constrains valid alternative solutions.
- **Prevent grading bypasses** — passing should require genuinely solving the problem, not exploiting loopholes.
- **Validate graders** — confirm they pass reference solutions and fail broken ones.
- **Calibrate LLM judges** against human experts regularly to keep model-grading aligned with human grading.

## Evals in the broader QA ecosystem

Automated evals are one layer of a larger system — like the Swiss Cheese Model from safety engineering, no single layer catches everything:

- **Automated evals** — pre-launch CI/CD, run on every change, fast iteration
- **Production monitoring** — post-launch detection of drift and unanticipated failures
- **A/B testing** — validates significant changes once there's enough traffic
- **User feedback** — surfaces problems evals didn't anticipate
- **Manual transcript review** — weekly sampling to spot failure modes/quality issues
- **Systematic human studies** — gold-standard calibration for subjective outputs

## Tools and frameworks mentioned

- **Harbor** — containerized environments with cloud-scale execution
- **Braintrust** — offline evaluation + production observability
- **LangSmith** — tracing/eval within the LangChain ecosystem
- **Langfuse** — open-source, self-hosted alternative
- **Arize Phoenix / AX** — LLM tracing and evaluation platforms

> Frameworks accelerate and standardize eval work, but they're "only as good as the eval tasks you run through them."
