# Invoice Agent

An accounts-payable review agent that investigates invoices the way a person
would — resolve the payee, check what they've been paid before, look for a
duplicate, reconcile against the purchase order, and read the policy — then
recommends approve, reject, or escalate, citing the clause it relied on.

Built on Claude with custom tools, a FastAPI + Postgres backend, a React
dashboard, and an eval harness that measures whether any of it actually works.

---

## The problem

Accounts payable is high-volume, low-variance work with expensive tails. Most
invoices are fine. A few are duplicates, a few don't match their purchase
order, and occasionally one is a payee nobody has verified. The cost of the
work isn't the thinking — it's that someone has to look at all of them to find
the few that matter.

That shape suits an LLM, with one catch that decides the whole design: **the
failure modes are not symmetric.** An invoice wrongly escalated costs a
reviewer a minute. An invoice wrongly approved is money out the door with
nobody watching. Any system here is only as good as its behavior on the second
kind.

## The intuition

Three ideas shaped this build, and each one is a constraint rather than a
feature.

**The rules live in a document, not in the code.** The tolerance for an
invoice-to-PO discrepancy is a business rule that Finance owns and revises. It
belongs in the policy the agent retrieves, not in a constant somewhere in the
repo. So `get_purchase_order` computes the variance — arithmetic is exactly
what a model gets subtly wrong — and deliberately refuses to judge it. Whether
that variance is acceptable has to come from the policy.

This is also what makes the retrieval measurable. An agent with the threshold
hardcoded passes the PO cases without ever consulting the policy, and the
before/after comparison measures nothing.

**Tools should hand back conclusions, not tables.** Every tool returns a small,
high-signal dict rather than rows. `get_invoice_history` returns count,
average, and range — not 200 invoices for the model to do arithmetic on.
`check_duplicate_invoice` distinguishes an exact resubmission from a near
match, because the two demand different actions and a boolean would collapse
that distinction.

Some things are deliberately *withheld*. `lookup_vendor` does not return its
similarity score: it's calibrated against a distribution the agent can't see,
and in an early run the agent read a perfectly good 0.42 trigram match as weak
evidence and cut its confidence. Deciding whether a match clears the bar is the
tool's job, already done.

**The agent proposes; the system decides.** A confidence floor overrides the
agent's own recommendation below a threshold, in both directions — a low
confidence rejection escalates too, because stalling a legitimate payment has
costs of its own. And no invoice is finalized by the agent at all: the only
code path that changes an invoice's status is the human approve/reject
endpoint.

## What it does

1. **Ingest** a messy invoice PDF. Text-layer extraction first; vision fallback
   for scans.
2. **Extract** fields — vendor, amount, currency, due date, PO number, line
   items — in one structured-output call.
3. **Investigate.** A tool-using agent resolves the vendor against the master
   file, compares the amount to payment history, checks for duplicates,
   reconciles against the PO, and retrieves the policy clauses that govern this
   invoice.
4. **Decide**, with a confidence score and reasoning that cites the section it
   relied on.
5. **Override** to `escalate` if confidence is below the floor, whatever the
   agent asked for.
6. **Route to a human**, who approves or rejects with a note. Every tool call
   and every human action is logged — the same transcript backs the dashboard,
   the audit log, and the eval harness.

### The agent's tools

| Tool | What it answers |
|---|---|
| `lookup_vendor` | Is this payee on file? Resolves printed names via trigram similarity |
| `draft_vendor` | Queue an unknown payee for human approval — does *not* make it payable |
| `get_invoice_history` | Is this amount normal for them? Aggregates only |
| `check_duplicate_invoice` | Have we already paid this? Distinguishes exact from near |
| `get_purchase_order` | How far does this diverge from the PO? Reports variance, never a verdict |
| `search_policy` | What do the written rules say? Returns clauses with their section, for citation |
| `submit_recommendation` | Records the decision, subject to the confidence floor |

---

## Architecture

<!-- Replace with the architecture diagram. -->
![Architecture](docs/images/architecture.png)

**Stack**

| Layer | |
|---|---|
| Agent | Claude Opus 5, via the Anthropic SDK's tool runner |
| Extraction | `pdfplumber` text layer, Claude vision fallback for scans |
| Embeddings | Voyage `voyage-3.5-lite`, 1024 dimensions |
| Retrieval | pgvector cosine similarity over the chunked policy corpus |
| Backend | FastAPI + SQLAlchemy (async), Alembic migrations |
| Database | Postgres 16 + pgvector, in Docker |
| Frontend | React 19 + TypeScript + Vite |
| Eval judge | Claude Sonnet 5 — deliberately not the model under test |

**RAG is policy-only.** Invoice text is never embedded. The agent already has
the invoice in its opening prompt, and retrieving text from *other* invoices
would put foreign amounts in front of an agent judging this one. The corpus is
a real published document — UNFPA's *Policy and Procedures on Accounts
Payable*, 15 pages — chunked on its own numbered headings so every retrieved
clause is citable.

---

## Evals

Twelve cases, three trials each. Every trial runs in a database emptied and
reseeded from scratch, inside a transaction that is rolled back afterwards —
two of the agent's tools answer questions *about accumulated state*, so a trial
that leaves rows behind doesn't merely pollute the next one, it changes what
the next one is testing.

### What each trial is graded on

| Grader | Question |
|---|---|
| `grade_outcome` | Did it reach the decision a careful reviewer would? |
| `grade_tool_calls` | Did it actually run the checks this case turns on? Presence only — extra tools aren't a failure |
| `grade_committed` | Did the agent decide, or did the iteration limit force `escalate`? Seven cases expect escalation, so without this an agent that never committed would score over half the suite |
| `grade_groundedness` | Is every claim traceable to a tool result? Judged by a second, cheaper model — this is the one that catches a right answer reached by recalling a plausible rule instead of retrieving one |

Failures carry a **severity**, because a single pass rate hides which kind you
got. Only a wrongful *approve* is `unsafe` — the one branch where money leaves
without anyone looking. Escalating something it could have decided, or refusing
something legitimate, is recoverable by the human who sees it next. Three
needless escalations is a tuning problem; three wrongful approvals is a system
you cannot deploy.

### The twelve cases

`policy` marks cases whose correct answer exists only in the AP policy document
— these are expected to fail before retrieval was added. That gap is the
measurement, not a defect.

| # | Case | Expect | What it probes |
|---|---|---|---|
| 01 | `clean_approve` | approve | Baseline. Everything reconciles — does the happy path clear without manufactured caution? |
| 02 | `exact_duplicate_reject` | reject | Same number *and* amount as a prior payment. A confirmed duplicate can be refused outright |
| 03 | `near_duplicate_escalate` | escalate | Same amount, suffixed number (`INV-1` vs `INV-1-A`). Suspicious but not proven — belongs in front of a human, not refused |
| 04 | `vendor_name_drift_approve` | approve | Invoice prints "Acme Inc"; the master file says "ACME Incorporated". Can it resolve a drifted name rather than treating it as unknown? *(The one failing case — see below)* |
| 05 | `vendor_not_on_file_escalate` | escalate | A genuinely unknown payee. An unknown vendor is a stop, not a guess |
| 06 | `po_variance_within_tolerance_approve` | approve | `policy` · $400 on a $6,000 PO. The lesser limit is 10% ($600), so this clears — the clean test that a rule was *read* rather than guessed |
| 07 | `po_variance_outside_tolerance_escalate` | escalate | `policy` · $3,000 on a $20,000 PO — 15% and $3,000, outside both bounds. No reading of §II lets this through |
| 08 | `po_variance_lesser_of_two_escalate` | escalate | `policy` · The precision test. $1,600 on a $40,000 PO is only 4% — comfortably inside the percentage — but the cap is the *lesser* of 10% or $1,000. An agent that skims "10 percent" approves this |
| 09 | `large_invoice_no_po_escalate` | escalate | `policy` · $40,000 with no PO. The policy defers the PO-required threshold to a document not in the corpus, so the right move is to escalate rather than invent a number — this tests whether it recognizes the edge of what it actually read |
| 10 | `amount_outlier_escalate` | escalate | $25,000 against a history of ~$1,000 payments. Judging an amount against the vendor's own pattern |
| 11 | `non_usd_currency_approve` | approve | `policy` · A EUR invoice against a EUR PO. §IV.F makes local currency the norm, so this is ordinary business — the currency should be cited, not flagged as an anomaly |
| 12 | `low_quality_scan_forced_escalate` | escalate | A scan so degraded no amount survived extraction. Nothing to check and nothing to be confident about — the confidence floor should carry this one |

Two rules govern the suite. **A case may only test a rule the policy actually
states** — a case asserting a rule the corpus doesn't contain is a broken task,
and it reads as an agent failure forever without ever being one. And **every
anomaly is paired with a near-identical case that should pass**: 06/07/08
differ only in how far the invoice diverges from its PO, 02 and 03 only in
whether the number matches exactly. Drop the pairs and an agent that escalates
everything scores well, which is the one-sided optimization the suite exists to
make impossible.

### Incremental Result Improvement

The suite was first run before and after `search_policy` was added, with
nothing else changed. It was re-run later against the current code, after the
schema gained the currency columns that case 11 turned out to need.

| | Baseline | With retrieval | Current |
|---|---|---|---|
| pass^3 | 9 / 12 | 10 / 12 | **11 / 12** |
| pass@1 | 75% | 89% | **92%** |
| policy-dependent cases | 2 / 5 | 4 / 5 | **5 / 5** |
| **unsafe approvals** | **3** | **0** | **0** |
| tool coverage | 7 / 12 | 12 / 12 | **12 / 12** |

The row that matters is unsafe approvals. At baseline the agent approved case
09's $40,000 invoice on all three trials, at 0.80–0.82 confidence — above the
escalation floor, so no human would ever have seen it. Its reasoning was sound
on its own terms: *"All applicable checks came back clean."* Every check it
could run had passed. The rule it needed existed only in the policy.

**Case 11** moved 0/3 → 3/3 in the re-run. It was never an agent failure: with
no currency column anywhere in the schema, a EUR invoice was compared against a
currency-less purchase order as bare numerals, producing a meaningless "0.0%
variance" that the agent correctly refused to treat as a passed check. The
system could not represent the fact the case turned on. Adding the columns —
and teaching `get_purchase_order` to report `currency_match` and decline to
compute a variance across units — fixed it.

**Case 04 is the remaining failure, and it no longer measures what it was
written to measure.** The case seeds a $500 invoice with a drifted vendor name
and no purchase order, and expects `approve`. All three trials escalate.

The drift itself resolves cleanly every time — `lookup_vendor` turns the
printed "Acme Inc" into `ACME Incorporated` with `match: "resolved"` in all
three trials, and the tool-coverage grader passes. The capability the case
exists to test works. What decides the outcome is a second variable the case was
never designed to probe: with no PO referenced, §III routes the invoice down
the separate non-PO path and defers the "was a purchase order *required* at
this amount?" threshold to the UNFPA Procurement Procedures — a document that
is not in the corpus. So the agent declines to assert that $500 sits below a
limit it cannot read, and says so explicitly rather than treating an
unperformable check as a passed one.

That is the same reasoning that eliminated all three unsafe approvals on case
09; 04 and 09 differ only in amount. The expectation is left as `approve`
rather than quietly rewritten to match the behaviour, so the suite reports
11/12 — but read as a statement about the agent, the case is reassuring rather
than damning: the vendor resolution passes, and the escalation names the single
reason it happened, which is exactly what a reviewer needs. A reviewer loses a
minute. Closing it properly means splitting the two variables apart — giving
the drift case a PO so it tests only the name — or supplying the missing
document, not tuning the agent.

Full analysis of the original comparison is in
[`finalResults.md`](finalResults.md). Raw data:
`backend/eval_results_{baseline,with_rag,with_rag_v2}.json`.

---

## Setup

### Prerequisites

Docker, Python 3.11, Node 20+, and API keys for Anthropic and Voyage.

### 1. Database

```bash
cd backend
docker compose up -d
docker compose ps          # confirm the db container is healthy
```

This starts Postgres 16 with pgvector. On a fresh volume it also creates the
separate eval database (see `initdb/`).

### 2. Environment

Create `backend/.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
DATABASE_URL=postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent
EVAL_DATABASE_URL=postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent_eval
```

`EVAL_DATABASE_URL` is not optional if you intend to run the tests or the eval
suite. Both empty every table before each trial, so they refuse to run against
the application database — see [`backend/README-eval-db.md`](backend/README-eval-db.md)
for the setup and the reasoning.

### 3. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

alembic upgrade head            # schema
python -m fixtures.load_policy  # chunk + embed the AP policy (one Voyage call)
python -m fixtures.seed_demo    # vendors, payment history, purchase orders

uvicorn app.main:app --reload   # serves on :8000
```

`uvicorn` is the ASGI server that actually runs the FastAPI app. The async
stack matters here: an upload returns `202` immediately and the agent runs as a
background task, which is what lets the dashboard poll a review while it's
still happening.

Check it: `curl localhost:8000/health` → `{"status":"ok"}`

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                     # serves on :5173
```

---

## Running things

**Tests** — 130 tests, no API calls:

```bash
cd backend && python -m pytest -q -m "not integration"
```

Integration tests hit paid APIs and are deselected by default.

**The eval suite** — 12 cases × 3 trials, roughly 20 minutes and real money:

```bash
cd backend && python -m app.eval.report <label>
```

Writes `eval_results_<label>.json`, which the next run can be diffed against.

**Reset the demo** — wipes every data-bearing table except the policy corpus,
then reseeds:

```bash
cd backend && python -m fixtures.seed_demo
```

---

## Layout

```
backend/
  app/
    agent/          prompts, tool implementations, the SDK tool runner, transcripts
    eval/           cases, suite, harness, graders, report
    rag/            chunking, embeddings, storage, search
    main.py         FastAPI routes
    models.py       SQLAlchemy models
  fixtures/         policy PDF, sample invoices, seed + load scripts
  tests/
frontend/src/       React dashboard
```

Further reading:

- [`Project.MD`](Project.MD) — the design document
- [`finalResults.md`](finalResults.md) — the before/after retrieval measurement
- [`architecture-tradeoffs.md`](architecture-tradeoffs.md) — design decisions and what each one costs
- [`backend/README-eval-db.md`](backend/README-eval-db.md) — why the tests and evals use a separate database
