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

## Does the retrieval earn its place?

Twelve cases, three trials each, run before and after `search_policy` was
added. Nothing else changed.

| | Baseline | With retrieval |
|---|---|---|
| pass^3 | 9 / 12 | **10 / 12** |
| policy-dependent cases | 2 / 5 | **4 / 5** |
| **unsafe approvals** | **3** | **0** |
| tool coverage | 7 / 12 | **12 / 12** |

The result that matters is the third row. At baseline the agent approved a
$40,000 invoice with no purchase order on all three trials, at 0.80–0.82
confidence — above the escalation floor, so no human would ever have seen it.
Its reasoning was sound on its own terms: *"All applicable checks came back
clean."* Every check it could run had passed. The rule it needed existed only
in the policy.

Full analysis, including the case that regressed and the one that turned out to
be a broken task, is in [`finalResults.md`](finalResults.md).

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
