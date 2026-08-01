# Invoice Agent — Design

A leaner weekend project scoped around four things: a dashboard on real business data, an agent you build custom tools for, an eval harness that actually tests it, and a FastAPI + Postgres backend underneath all of it. Domain stays AP invoices (still maps to "100% of A/P invoices processed"), but the messy-document-ingestion effort that used to be a side quest is now the source for a RAG tool the agent uses — not a separate concern.

## What it does

1. Ingest a messy invoice PDF; extract text via a hybrid pipeline (text-layer first, vision fallback for scans)
2. Run one structured-output LLM call on the extracted text to get fields (vendor, amount, due date, line items)
3. Chunk + embed the raw document text (and a small AP policy doc) into `pgvector` for retrieval
4. A tool-using agent investigates the invoice — resolving the vendor, checking history, checking for duplicates/PO matches, retrieving grounding context from the documents — and submits a decision + confidence + reasoning
5. Below a confidence threshold, the system overrides the agent's decision to `escalate`, regardless of what it said
6. A human approves/rejects via a dashboard
7. Every tool call and every human action is logged — the same transcript backs the dashboard detail view, the audit log, and the eval harness

## Data model — Postgres

- `vendors` — id, name, normalized_name, bank_details
- `invoices` — id, vendor_id, amount, due_date, status (`pending`/`approved`/`rejected`/`escalated`), confidence_score, raw_pdf_path
- `line_items` — id, invoice_id, description, amount
- `documents` — id, invoice_id, doc_type (`invoice`/`policy`), chunked text + `pgvector` embeddings — powers the agent's retrieval tool
- `agent_runs` — id, invoice_id, source (`live`/`eval`), transcript (tool calls, results, reasoning, final recommendation) — read by the dashboard, the audit log, and the eval harness
- `audit_log` — id, invoice_id, actor (`agent`/`human`), action, before/after state, timestamp

## Backend — FastAPI

- `POST /invoices` — upload a PDF; extraction + agent run as a background task so the request doesn't block
- `GET /invoices?status=pending` — list for the dashboard
- `GET /invoices/{id}` — extracted fields, agent recommendation, confidence, retrieved context, full tool-call transcript
- `POST /invoices/{id}/approve` / `.../reject` — writes to `audit_log`
- `POST /eval/run` — runs the eval suite against the current agent, tags resulting runs `source='eval'`, returns pass/fail per case

## Extraction, chunking, RAG

**Pipeline:** try `pdfplumber`/`pypdf` for the text layer first (free, instant, works for born-digital PDFs). If extracted text is empty or below a length/quality threshold — the signal for a scanned/image PDF — fall back to sending the page image to Claude for transcription. Either path ends in `raw_text`, which feeds two things: one structured-output call for the fields, and chunking for the vector store.

**Chunking:** invoices are short, so fixed-size chunks (~500 chars, small overlap) are enough; the policy doc gets split by paragraph/section since it's closer to prose.

**Normalization:** deliberately *not* done at ingestion. The raw extracted vendor name is left as-is; resolving it against the `vendors` table (fuzzy match via `pg_trgm`) happens at decision time inside the agent's `lookup_vendor` tool. That keeps ingestion simple and makes the mismatch something the agent has to notice and reason about — which is also what the eval harness tests.

This hybrid approach is the one with an actual cost/failure-mode story: cheap path for clean PDFs, expensive fallback only when needed, real "handle two extraction failure modes" experience instead of routing everything through vision by default.

## The agent + tools

Raw Anthropic SDK tool-use loop — send the invoice + system prompt, read `tool_use` blocks, execute the matching function, feed back `tool_result`, repeat until `submit_recommendation` is called or an 8-turn cap is hit.

- `lookup_vendor(vendor_name)` — fuzzy-resolves against `vendors`; returns matched id/name/bank details or "no match" below a similarity threshold. This is where vendor-name drift surfaces.
- `get_invoice_history(vendor_id, lookback_days)` — summary stats only (avg amount, count, most recent date), not raw rows — the agent reasons over a signal, not a table it has to summarize itself.
- `check_duplicate_invoice(vendor_id, amount, invoice_number)` — flags a likely-duplicate match.
- `get_purchase_order(po_number)` — does a referenced PO exist, does its amount match.
- `search_documents(query, invoice_id)` — RAG tool: embeds `query`, similarity-searches that invoice's chunks plus the policy doc in `pgvector`, returns the top 3-5 chunks. How the agent grounds itself in exact wording (payment terms, a handwritten note, a policy threshold) instead of only the structured fields.
- `submit_recommendation(decision, confidence, reasoning)` — terminal tool; `decision` is `approve`/`reject`/`escalate`.

Tool responses are designed to be high-signal (3-5 fields that matter, not a dump) and hard to misuse. Confidence below threshold forces `escalate` server-side — the agent proposes, the system enforces the "low-confidence must go to a human" rule.

## Eval harness

An **agent eval** (multi-turn, tool use, state to check) run as a **capability eval** — the goal is to see what the agent handles well and where it breaks, not to guard a baseline that doesn't exist yet.

Balanced test cases (positive and negative, not just anomalies):

| # | Case | Expected outcome | Should call |
|---|---|---|---|
| 1 | Clean invoice, known vendor, matches PO | approve, high confidence | `lookup_vendor`, `get_purchase_order` |
| 2 | Duplicate of an already-paid invoice | reject | `check_duplicate_invoice` |
| 3 | Vendor name drift ("Acme Inc" vs "ACME Incorporated") | approve — resolved, not flagged | `lookup_vendor` |
| 4 | Amount 5x this vendor's historical average | escalate | `get_invoice_history` |
| 5 | PO number doesn't exist / amount mismatch | escalate or reject | `get_purchase_order` |
| 6 | Bank details differ from vendor record | reject (fraud signal) | `lookup_vendor` |
| 7 | >$10k invoice, no evidence of second approval | escalate | `search_documents` (policy) |
| 8 | >$10k invoice, second approval documented | approve — negative case for #7 | `search_documents` |
| 9 | Low-quality scan, genuinely uncertain extraction | escalate (confidence-forced) | — |

**Mechanics:** each trial seeds a clean DB state (fresh transaction, rolled back after) so history/duplicate checks can't leak between trials. Runs go through `POST /eval/run`; results land in `agent_runs` tagged `source='eval'`, reusing the same transcript structure as live runs.

**Graders:** mostly code-based, read off `agent_runs.transcript` — tool-call verification (were the expected tools actually called) and outcome match (does the final, post-escalation-override decision match the reference). One LLM-rubric grader, used only for reasoning groundedness (is the reasoning backed by what the tools returned, not hallucinated), calibrated by hand against a manual read of 5-6 transcripts.

**Non-determinism:** 3 trials per case, reporting both pass@1 (raw accuracy) and pass^3. pass^3 is the one that matters most here — a high-confidence wrong call skips human review entirely, so a case that only passes 1 of 3 trials is exactly the reliability failure confidence-based escalation exists to catch.

## Dashboard — React/TypeScript

Table of pending invoices, a detail view (extracted fields, agent recommendation, confidence badge, retrieved context, full tool-call transcript), approve/reject buttons. No separate analytics view — the invoice records themselves are the business data.

## Weekend timebox

- **Saturday AM**: DB schema + FastAPI backend + hybrid extraction pipeline
- **Saturday PM**: chunking + `pgvector` embedding, structured field extraction wired up and tested on messy samples
- **Saturday evening**: agent + tools built and running end-to-end on a few real invoices
- **Sunday AM**: eval harness — write the 9 cases, code-based graders, the LLM groundedness grader, run and read transcripts
- **Sunday PM**: React dashboard wired to the API, audit log

The agent's tool design and the eval harness are the highest-value blocks — protect that time before dashboard polish.

## Bottom line

Four things, built deep instead of many things built shallow: a dashboard on real invoice data, a hand-built tool-use loop (not a framework's agent executor), a RAG pipeline feeding the agent's own retrieval tool, and an eval harness with balanced cases, code-based + calibrated LLM graders, and pass@1/pass^3 reporting. This is a stronger "tell me about a project" answer than the broader version — narrower scope, but everything in it is something you can open the transcript for and explain.
