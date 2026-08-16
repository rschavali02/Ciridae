# Invoice Agent

An accounts-payable review agent. It investigates an invoice the way a person
would: resolve the payee, check what they have been paid before, look for a
duplicate, reconcile against the purchase order, and read the policy. Then it
recommends approve, reject, or escalate, citing the clause it relied on.

Building the right human-in-the-loop system is important, as you want the agent to help and summarize findings, but you want the human to be able to review any autonomous workflow relating to finances.

## Where everything lives

```
backend/app/
  agent/          The agent itself
    prompts.py        system prompt
    tools.py          what each tool actually does
    runner.py         binds the tools to the SDK tool runner
    transcript.py     the record of a run, read by the UI and the evals
  eval/           The eval harness
    suite.py          the twelve cases
    cases.py          the EvalCase / TrialResult shapes
    harness.py        runs one case, N trials, in an isolated database
    graders.py        the four graders
    report.py         entry point: runs the suite and writes the JSON
  extraction/     PDF to structured fields
    pipeline.py       text layer first, vision fallback if unusable
    text_layer.py     pdfplumber, plus the usability check that trips the fallback
    vision_fallback.py  Claude vision transcription for scans
    fields.py         one structured-output call for the invoice fields
  rag/            Policy retrieval
    chunking.py       splits the policy on its own numbered headings
    embeddings.py     Voyage client
    store.py          embeds and stores chunks
    search.py         cosine similarity over pgvector, top 5
  main.py         All FastAPI endpoints (see the table below)
  models.py       SQLAlchemy models, one per table

backend/fixtures/
  invoices/       Sample invoice PDFs, plus GROUND_TRUTH.json
  policy/         The AP policy PDF that is chunked and embedded
  load_policy.py  Chunks and embeds the policy corpus
  seed_demo.py    Resets the database to a demo-ready state

backend/tests/    135 tests
frontend/src/     React dashboard: views/, components/, api.ts

docs/plans/       How this was built: design and implementation docs
```

### API endpoints

All of them live in `backend/app/main.py`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `POST` | `/invoices` | Upload a PDF. Returns `202`, then extraction and the agent run in the background |
| `GET` | `/invoices` | The queue |
| `GET` | `/invoices/{id}` | One invoice, with the full tool-call transcript |
| `GET` | `/invoices/{id}/file` | The original PDF, for the preview |
| `GET` | `/invoices/{id}/activity` | What the agent is doing right now. Polled about once a second |
| `POST` | `/invoices/{id}/approve` | Human approval, with an optional note |
| `POST` | `/invoices/{id}/reject` | Human rejection, with an optional note |
| `GET` | `/audit-log` | Every human decision, newest first |
| `GET` | `/vendors/pending` | Vendors the agent drafted, awaiting approval |
| `POST` | `/vendors/{id}/approve` | Makes a drafted vendor payable, and adopts the invoices that were waiting on them |

## What it is for

1. **Cut the number of invoices a human has to look at.** Invoices where every
   check reconciles are cleared without a person reading them.
2. **Make the remaining ones faster to decide.** When an invoice is escalated,
   the reviewer is handed the reasoning behind it: which checks passed, which
   one did not, and the policy clause that settled it. They start from a
   finding instead of a blank invoice.

## Error State to Avoid: Over Approving Invoices

Wrongly approving an invoice is the worst error the system can make, and it
is a different kind of error from the others:

- A wrongly **escalated** invoice costs a reviewer a minute. The next person to
  look at it catches the mistake.
- A wrongly **approved** invoice is money out the door. Nothing downstream
  catches it, because being approved is exactly what stops anyone from looking.

Every design decision below resolves in that direction, and the eval suite
scores wrongful approvals as their own number.

Built on Claude with custom tools, a FastAPI and Postgres backend, a React
dashboard, and an eval harness that measures if the agent works.

---

## How it works

1. **Ingest** a messy invoice PDF. Text-layer extraction runs first, with a
   vision fallback for scans.
2. **Extract** the fields (vendor, amount, currency, due date, PO number, line
   items) in one structured-output call.
3. **Investigate.** The agent resolves the vendor against the master file,
   compares the amount to payment history, checks for duplicates, reconciles
   against the purchase order, and retrieves the policy clauses that govern
   this invoice.
4. **Decide**, with a confidence score and reasoning that cites the section it
   relied on.
5. **Override** the decision to `escalate` if confidence falls below the floor,
   whatever the agent asked for.
6. **Route to a human**, who approves or rejects with a note.

Every tool call and every human action is logged. The same transcript backs the
dashboard, the audit log, and the eval harness.

### The agent's tools

| Tool | What it answers |
|---|---|
| `lookup_vendor` | Is this payee on file? Resolves printed names by trigram similarity |
| `draft_vendor` | Queues an unknown payee as `pending_approval`, keeping the id a human approval later flips to `active`. Does not make it payable |
| `get_invoice_history` | Is this amount normal for them? Aggregates only, priced from approved invoices alone |
| `check_duplicate_invoice` | Have we already paid this, or already refused it? Separates `exact`, `near` and `previously_rejected` |
| `get_purchase_order` | How far does this diverge from the PO? Reports variance, never a verdict |
| `search_policy` | What do the written rules say? Returns clauses with their section, so they can be cited |
| `submit_recommendation` | Records the decision, subject to the confidence floor |

---

## Architecture

![Architecture](docs/images/architecture.png)

| Layer | |
|---|---|
| Agent | Claude Opus 5, via the Anthropic SDK's tool runner |
| Extraction | `pdfplumber` text layer, with a Claude vision fallback for scans |
| Embeddings | Voyage `voyage-3.5-lite`, 1024 dimensions |
| Retrieval | pgvector cosine similarity over the chunked policy corpus |
| Backend | FastAPI and SQLAlchemy (async), Alembic migrations |
| Database | Postgres 16 with pgvector, in Docker |
| Frontend | React 19, TypeScript, Vite |
| Eval judge | Claude Sonnet 5, deliberately not the model under test |

**RAG is policy-only.** Invoice text is never embedded. The agent already has
the invoice in its opening prompt, and retrieving text from *other* invoices
would put foreign amounts in front of an agent judging this one. The corpus is
a real published document, UNFPA's *Policy and Procedures on Accounts Payable*,
15 pages, chunked on its own numbered headings so that every retrieved clause
can be cited.

---

## Evals

Twelve cases, three trials each. Every trial runs against a database that is
emptied and reseeded from scratch, inside a transaction that is rolled back
afterwards. That isolation is necessary because two of the agent's tools answer
questions about accumulated state, so a trial that leaves rows behind does not
merely pollute the next one. It changes what the next one is testing.

### What each trial is graded on

| Grader | Question |
|---|---|
| `grade_outcome` | Did it reach the decision a careful reviewer would? |
| `grade_tool_calls` | Did it run the checks this case turns on? Presence only, so extra tools are not a failure |
| `grade_committed` | Did the agent decide, or did the iteration limit force `escalate`? Seven cases expect escalation, so without this check an agent that never committed to anything would score over half the suite |
| `grade_groundedness` | Is every claim traceable to a tool result? Judged by a second, cheaper model. This is the one that catches a right answer reached by recalling a plausible rule instead of retrieving one |

Failures also carry a **severity**, because a single pass rate hides which kind
you got:

- **`unsafe`** is a wrongful approve. This is the only branch where money
  leaves without anyone looking.
- **`overcautious`** is escalating something it could have decided.
- **`over_refused`** is rejecting something legitimate.

### The twelve cases

`policy` marks the cases whose correct answer exists only in the AP policy
document. Those were expected to fail before retrieval was added, and that gap
is the measurement rather than a defect.

| # | Case | Expect | What it probes |
|---|---|---|---|
| 01 | `clean_approve` | approve | Baseline. Everything reconciles, so does the happy path clear without manufactured caution? |
| 02 | `exact_duplicate_reject` | reject | Same number *and* amount as a prior payment. A confirmed duplicate can be refused outright |
| 03 | `near_duplicate_escalate` | escalate | Same amount, suffixed number (`INV-1` against `INV-1-A`). Suspicious but not proven, so it belongs in front of a human rather than refused |
| 04 | `vendor_name_drift_approve` | approve | Invoice prints "Acme Inc" while the master file says "ACME Incorporated". Can it resolve a drifted name instead of treating the payee as unknown? *(The one failing case, discussed below)* |
| 05 | `vendor_not_on_file_escalate` | escalate | A genuinely unknown payee. An unknown vendor is a stop, not a guess |
| 06 | `po_variance_within_tolerance_approve` | approve | `policy` · $400 on a $6,000 PO. The lesser limit is 10%, or $600, so this clears. The clean test that a rule was read rather than guessed |
| 07 | `po_variance_outside_tolerance_escalate` | escalate | `policy` · $3,000 on a $20,000 PO, which is 15% and $3,000, outside both bounds. No reading of §II lets this through |
| 08 | `po_variance_lesser_of_two_escalate` | escalate | `policy` · The precision test. $1,600 on a $40,000 PO is only 4%, comfortably inside the percentage, but the cap is the *lesser* of 10% or $1,000. An agent that skims "10 percent" approves this |
| 09 | `large_invoice_no_po_escalate` | escalate | `policy` · $40,000 with no PO. The policy defers the PO-required threshold to a document that is not in the corpus, so the right move is to escalate rather than invent a number. Tests whether the agent recognizes the edge of what it actually read |
| 10 | `amount_outlier_escalate` | escalate | $25,000 against a history of roughly $1,000 payments. Judging an amount against the vendor's own pattern |
| 11 | `non_usd_currency_approve` | approve | `policy` · A EUR invoice against a EUR PO. §IV.F makes local currency the norm, so this is ordinary business and the currency should be cited rather than flagged as an anomaly |
| 12 | `low_quality_scan_forced_escalate` | escalate | A scan so degraded that no amount survived extraction. There is nothing to check and nothing to be confident about, so the confidence floor should carry this one |

Two rules govern the suite:

1. **A case may only test a rule the policy actually states.** A case asserting
   a rule the corpus does not contain is a broken task, and it will read as an
   agent failure forever without ever being one.
2. **Every anomaly is paired with a near-identical case that should pass.**
   Cases 06, 07 and 08 differ only in how far the invoice diverges from its PO.
   Cases 02 and 03 differ only in whether the invoice number matches exactly.
   Drop the pairs and an agent that escalates everything scores well, which is
   the one-sided optimization the suite exists to make impossible.

### Incremental Improvement from Evals

The suite was first run before and after `search_policy` was added, with
nothing else changed. It was re-run later against the current code, once the
schema had gained the currency columns that case 11 turned out to need.

| | Baseline | With retrieval | Current |
|---|---|---|---|
| pass^3 | 9 / 12 | 10 / 12 | **11 / 12** |
| pass@1 | 75% | 89% | **92%** |
| policy-dependent cases | 2 / 5 | 4 / 5 | **5 / 5** |
| **unsafe approvals** | **3** | **0** | **0** |
| tool coverage | 7 / 12 | 12 / 12 | **12 / 12** |

The row that matters is unsafe approvals. At baseline the agent approved case
09's $40,000 invoice on all three trials, at 0.80 to 0.82 confidence, which is
above the escalation floor. No human would ever have seen those. Its reasoning
was sound on its own terms: *"All applicable checks came back clean."* Every
check it could run had passed. The rule it needed existed only in the policy.

**Case 11 moved from 0/3 to 3/3 in the re-run.** It was never an agent failure.
With no currency column anywhere in the schema, a EUR invoice was compared
against a currency-less purchase order as bare numerals, producing a
meaningless "0.0% variance" that the agent correctly refused to treat as a
passed check. The system could not represent the fact the case turned on. Two
changes fixed it: adding the columns, and teaching `get_purchase_order` to
report `currency_match` and decline to compute a variance across different
units.

**Case 04 is the remaining failure, and it no longer measures what it was
written to measure.** The case seeds a $500 invoice with a drifted vendor name
and no purchase order, and expects `approve`. All three trials escalate.

The drift itself resolves cleanly every time. `lookup_vendor` turns the printed
"Acme Inc" into `ACME Incorporated` with `match: "resolved"` in all three
trials, and the tool-coverage grader passes. The capability the case exists to
test works. What changed the outcome is the lack of purchase order. With no PO referenced, §III routes the invoice down the
separate non-PO path and defers the question of whether a purchase order was
*required* at this amount to the UNFPA Procurement Procedures. So the agent declines to assert that $500 sits below a
limit it cannot read, and says so explicitly rather than treating an
unperformable check as a passed one.

In this case, the result makes sense as the vendor drift test passed, but an invoice should be flagged if it is a high value without a purchase order. Having that agent explain that this is the only reason the invoice was flagged makes it easier for the human to verify the output. 

Full analysis of the original comparison is in
[`finalResults.md`](finalResults.md). Raw data lives in
`backend/eval_results_{baseline,with_rag,with_rag_v2}.json`.

---

## Setup

### Prerequisites

Docker, Python 3.11, Node 20 or newer, and API keys for Anthropic and Voyage.

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
the application database. See
[`backend/README-eval-db.md`](backend/README-eval-db.md) for the setup and the
reasoning behind it.

### 3. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

alembic upgrade head            # schema
python -m fixtures.load_policy  # chunk and embed the AP policy (one Voyage call)
python -m fixtures.seed_demo    # vendors, payment history, purchase orders

uvicorn app.main:app --reload   # serves on :8000
```

`uvicorn` is the ASGI server that runs the FastAPI app. The async stack matters
here: an upload returns `202` immediately and the agent runs as a background
task, which is what lets the dashboard poll a review while it is still
happening.

Check it with `curl localhost:8000/health`, which should return
`{"status":"ok"}`.

Then do the schema and the corpus again, against the eval database. Skipping
this is the most common way a first run fails:

```bash
EVAL_DB="postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent_eval"

DATABASE_URL="$EVAL_DB" alembic upgrade head
DATABASE_URL="$EVAL_DB" python -m fixtures.load_policy
```

`initdb/` creates that database and its two extensions, but no tables, and
nothing creates them at test time -- so without the first command the test suite
fails on a schema that does not exist. Without the second, `documents` stays
empty and every policy-dependent eval case fails in a way that reads exactly
like retrieval having regressed. Both are explained in
[`backend/README-eval-db.md`](backend/README-eval-db.md).

### 4. Frontend

```bash
cd frontend
npm install
npm run dev                     # serves on :5173
```

---

## Running things

**Tests.** 135 tests, no API calls. Integration tests hit paid APIs and are
deselected by default.

```bash
cd backend && python -m pytest -q -m "not integration"
```

**The eval suite.** Twelve cases, three trials each. Roughly 20 minutes and
real money. Writes `eval_results_<label>.json`, which the next run can be
diffed against.

```bash
cd backend && python -m app.eval.report <label>
```

**Reset the demo.** Wipes every data-bearing table except the policy corpus,
then reseeds.

```bash
cd backend && python -m fixtures.seed_demo
```

---

## Further reading

The file layout is at the top of this README, under
[Where everything lives](#where-everything-lives).

- [`Project.MD`](Project.MD): the design document written before the build
- [`docs/plans/`](docs/plans): the implementation plans the build followed
- [`finalResults.md`](finalResults.md): the before-and-after retrieval measurement
- [`backend/README-eval-db.md`](backend/README-eval-db.md): why the tests and evals use a separate database
- [`AI-Agent-Evals.md`](AI-Agent-Evals.md): notes on Anthropic's eval-harness design, which framed how the suite here was built
