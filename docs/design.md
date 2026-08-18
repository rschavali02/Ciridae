# Invoice Agent — Design

A leaner weekend project scoped around four things: a dashboard on real business data, an agent you build custom tools for, an eval harness that actually tests it, and a FastAPI + Postgres backend underneath all of it. Domain stays AP invoices (still maps to "100% of A/P invoices processed"). Messy-document extraction feeds the agent's structured facts; RAG supplies the *rules* it judges those facts against.

## What it does

1. Ingest a messy invoice PDF; extract text via a hybrid pipeline (text-layer first, vision fallback for scans)
2. Run one structured-output LLM call on the extracted text to get fields (vendor, amount, due date, line items)
3. Chunk + embed the AP policy corpus into `pgvector` for retrieval
4. A tool-using agent investigates the invoice — resolving the vendor, checking history, checking for duplicates/PO matches, retrieving the policy clauses that govern this invoice — and submits a decision + confidence + reasoning citing the clause it relied on
5. Below a confidence threshold, the system overrides the agent's decision to `escalate`, regardless of what it said
6. A human approves/rejects via a dashboard
7. Every tool call and every human action is logged — the same transcript backs the dashboard detail view, the audit log, and the eval harness

## Data model — Postgres

- `vendors` — id, name, normalized_name, bank_details
- `invoices` — id, vendor_id, amount, due_date, status (`pending`/`approved`/`rejected`/`escalated`), confidence_score, raw_pdf_path
- `line_items` — id, invoice_id, description, amount
- `documents` — id, section, chunk_text + `pgvector` embedding — the chunked AP policy corpus, powers the agent's retrieval tool
- `agent_runs` — id, invoice_id, source (`live`/`eval`), transcript (tool calls, results, reasoning, final recommendation) — read by the dashboard, the audit log, and the eval harness
- `audit_log` — id, invoice_id, actor (`agent`/`human`), action, before/after state, timestamp

## Backend — FastAPI

- `POST /invoices` — upload a PDF; extraction + agent run as a background task so the request doesn't block
- `GET /invoices?status=pending` — list for the dashboard
- `GET /invoices/{id}` — extracted fields, agent recommendation, confidence, retrieved context, full tool-call transcript
- `POST /invoices/{id}/approve` / `.../reject` — writes to `audit_log`
- `POST /eval/run` — runs the eval suite against the current agent, tags resulting runs `source='eval'`, returns pass/fail per case

## Extraction, chunking, RAG

**Pipeline:** try `pdfplumber`/`pypdf` for the text layer first (free, instant, works for born-digital PDFs). If extracted text is empty or below a length/quality threshold — the signal for a scanned/image PDF — fall back to sending the page image to Claude for transcription. Either path ends in `raw_text`, which feeds one structured-output call for the fields.

**RAG is policy-only — the invoice text is deliberately *not* embedded.** The agent already receives the invoice's full extracted text and fields in its opening prompt, so retrieving that same text back through a tool would be a round-trip to fetch something already in context. Worse, retrieving raw text from *other* invoices would put foreign amounts and dates in front of an agent judging this one — a hallucination risk that the groundedness grader exists to catch.

The corpus is a real published policy: **UNFPA's *Policy and Procedures on Accounts Payable*** (`backend/fixtures/policy/FINA_Accounts_Payable.pdf`), 15 pages / ~26,600 characters of extracted text. Real rather than synthetic for two reasons. It's unambiguously too large to inject into every prompt, which settles the "is RAG actually necessary" question a four-bullet policy leaves open. And it's messy in the ways real corpora are messy — which is the point of the exercise.

**Chunking:** the naive approach (split on `\n\n`, one chunk per paragraph) fails outright here: PDF extraction produces **zero blank lines**, so paragraph splitting returns the entire document as a single chunk. Instead, split on the document's own numbered headings (`I. Purpose`, `A. Segregation of duties`, `Step 3:`), which survive extraction intact, and rejoin the wrapped lines between them into flowing text. Each chunk carries its heading so retrieved clauses are citable. Three cleanups the real document forces: strip the page header/footer repeated across all 15 pages (otherwise it gets embedded 15 times and pollutes every search), drop table-of-contents entries (they look like headings but trail dot leaders), and sub-split over-long sections on sentence boundaries.

**Normalization:** deliberately *not* done at ingestion. The raw extracted vendor name is left as-is; resolving it against the `vendors` table (fuzzy match via `pg_trgm`) happens at decision time inside the agent's `lookup_vendor` tool. That keeps ingestion simple and makes the mismatch something the agent has to notice and reason about — which is also what the eval harness tests.

This hybrid approach is the one with an actual cost/failure-mode story: cheap path for clean PDFs, expensive fallback only when needed, real "handle two extraction failure modes" experience instead of routing everything through vision by default.

## The agent + tools

The agent uses the Anthropic SDK's **Tool Runner** (`client.beta.messages.tool_runner`) — the documented default for a custom-tool agent. It drives the request → execute → loop cycle, so there's no hand-written `while`, no hand-maintained JSON schema array, and no name→function dispatch table.

Each tool's schema is derived from its Python signature and docstring, which creates one design constraint worth understanding: **tool parameters must be exactly what Claude should supply.** Our tools need a DB session and a transcript to write to, neither of which the model should see or invent. So the plain tool functions stay as-is (and stay unit-testable), and a thin `build_tools()` layer wraps each one in a decorated closure that binds session and transcript invisibly. That's the general answer to "my tool needs context the model shouldn't control."

Docstrings become load-bearing: the docstring *is* the description Claude reads when deciding whether to call a tool, and each `Args:` line is a parameter description. They're written prescriptively — when to call the tool, not just what it does.

- `lookup_vendor(vendor_name)` — fuzzy-resolves against `vendors`; returns matched id/name/bank details or "no match" below a similarity threshold. This is where vendor-name drift surfaces.
- `get_invoice_history(vendor_id, lookback_days)` — summary stats only (avg amount, count, most recent date), not raw rows — the agent reasons over a signal, not a table it has to summarize itself.
- `check_duplicate_invoice(vendor_id, amount, invoice_number)` — flags a likely-duplicate match.
- `get_purchase_order(po_number)` — does a referenced PO exist, does its amount match.
- `search_policy(query)` — RAG tool: embeds `query`, similarity-searches the policy corpus in `pgvector`, returns the top 3-5 clauses with their section headings. How the agent finds the rules that govern this invoice, so its decision cites a written provision rather than improvised judgment.
- `submit_recommendation(decision, confidence, reasoning)` — terminal tool; `decision` is `approve`/`reject`/`escalate`.

Tool responses are designed to be high-signal (3-5 fields that matter, not a dump) and hard to misuse. Confidence below threshold forces `escalate` server-side — the agent proposes, the system enforces the "low-confidence must go to a human" rule.

## Eval harness

An **agent eval** (multi-turn, tool use, state to check) run as a **capability eval** — the goal is to see what the agent handles well and where it breaks, not to guard a baseline that doesn't exist yet.

Balanced test cases (positive and negative, not just anomalies):

Cases test rules the policy **actually states** — a case asserting a rule the corpus doesn't contain is a broken task, not an agent failure. Each anomaly case is paired with a near-identical case that should pass, so the agent can't score well by flagging everything.

The governing clause for most of these is §II: *"discrepancies between the vendor invoice and the purchase order greater than 10 percent or $1,000 USD or equivalent in local currency (the lesser of the two) must be resolved before the payment can be processed."*

| # | Case | Expected outcome | Should call |
|---|---|---|---|
| 1 | Clean invoice, known vendor, PO matches exactly | approve, high confidence | `lookup_vendor`, `get_purchase_order` |
| 2 | Exact duplicate of an already-paid invoice | reject | `check_duplicate_invoice` |
| 3 | Near-duplicate — same vendor/amount, invoice number suffixed `-A` | escalate — pairs with #2 | `check_duplicate_invoice` |
| 4 | Vendor name drift ("Acme Inc" vs "ACME Incorporated") | approve — unambiguous match, not an anomaly | `lookup_vendor` |
| 5 | Vendor absent from the vendor master file | escalate — §III.A Step 1 requires vendor set up before payment | `lookup_vendor`, `search_policy` |
| 6 | PO variance 6% / $400 — inside **both** limits | approve (§II) | `get_purchase_order`, `search_policy` |
| 7 | PO variance 15% / $3,000 — outside both limits | escalate (§II) — pairs with #6 | `get_purchase_order`, `search_policy` |
| 8 | PO variance 4% / $2,500 — inside the percentage, outside the dollar cap | escalate — "**the lesser of the two**" governs; tests whether the agent read the clause precisely rather than pattern-matching on 10% | `get_purchase_order`, `search_policy` |
| 9 | Invoice above the PO-required threshold with no PO reference | escalate — the policy defers the threshold to the Procurement Procedures, a document not in the corpus, so it can't be self-verified | `search_policy` |
| 10 | Amount 5x this vendor's historical average | escalate | `get_invoice_history` |
| 11 | Invoice denominated in non-USD currency | approve, with the currency rule cited (§IV.F) | `search_policy` |
| 12 | Low-quality scan, amount illegible | escalate (confidence-forced) | — |

Cases 8 and 9 are the interesting ones. #8 fails any agent that skims "10 percent" and ignores the "lesser of the two" qualifier — a precision-of-reading test that a synthetic policy with one clean threshold couldn't produce. #9 tests whether the agent recognizes the limits of its own corpus instead of inventing a threshold, which is exactly the behavior the groundedness grader checks for.

**Mechanics:** each trial seeds a clean DB state (fresh transaction, rolled back after) so history/duplicate checks can't leak between trials. Runs go through `POST /eval/run`; results land in `agent_runs` tagged `source='eval'`, reusing the same transcript structure as live runs.

**Graders:** mostly code-based, read off `agent_runs.transcript` — tool-call verification (were the expected tools actually called) and outcome match (does the final, post-escalation-override decision match the reference). One LLM-rubric grader, used only for reasoning groundedness (is the reasoning backed by what the tools returned, not hallucinated), calibrated by hand against a manual read of 5-6 transcripts. Groundedness is sharper now that the policy is real: the agent should cite the clause it relied on, and a decision justified by a rule that isn't in the retrieved chunks is a clean fail.

**Non-determinism:** 3 trials per case, reporting both pass@1 (raw accuracy) and pass^3. pass^3 is the one that matters most here — a high-confidence wrong call skips human review entirely, so a case that only passes 1 of 3 trials is exactly the reliability failure confidence-based escalation exists to catch.

## Dashboard — React/TypeScript

Table of pending invoices, a detail view (extracted fields, agent recommendation, confidence badge, retrieved context, full tool-call transcript), approve/reject buttons. No separate analytics view — the invoice records themselves are the business data.

## Weekend timebox

- **Saturday AM**: DB schema + FastAPI backend + hybrid extraction pipeline
- **Saturday PM**: chunking + `pgvector` embedding, structured field extraction wired up and tested on messy samples
- **Saturday evening**: agent + tools built and running end-to-end on a few real invoices
- **Sunday AM**: eval harness — write the 12 cases, code-based graders, the LLM groundedness grader, run and read transcripts
- **Sunday PM**: React dashboard wired to the API, audit log

The agent's tool design and the eval harness are the highest-value blocks — protect that time before dashboard polish.

## Bottom line

Four things, built deep instead of many things built shallow: a dashboard on real invoice data, an agent built on the SDK's Tool Runner with hand-designed tools, a RAG pipeline over a corpus that genuinely needs retrieval, and an eval harness with paired cases, code-based + calibrated LLM graders, and pass@1/pass^3 reporting. This is a stronger "tell me about a project" answer than the broader version — narrower scope, but everything in it is something you can open the transcript for and explain, including *why* RAG points at the policy and not at the invoices.
