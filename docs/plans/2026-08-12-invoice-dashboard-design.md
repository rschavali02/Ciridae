# Invoice dashboard and vendor onboarding — design

The backend can review an invoice and explain itself, and the eval suite measures how
well. None of that is visible: there is no way to put an invoice in and watch what
happens to it. This phase builds that surface.

**Goal.** Upload an invoice, watch the agent work in near real time, and see the
outcome — cleared for payment, or held with the reason. Where the agent meets an
unknown payee it drafts a vendor record for a human to approve, never creating one
itself.

**Not in scope.** Payment execution. `approved` is terminal and means *cleared for
payment*; nothing in this system moves money. Batch upload is also out — one invoice
at a time, since persistence gives an accumulating queue without batch machinery.

---

## What already exists

Worth stating plainly, because it determines how much of this is new work.

- **The database is real and populated.** `invoices`, `vendors`, `purchase_orders`,
  `line_items`, `agent_runs`, `audit_log`, `documents`, migrations at head.
- **The transcript already records everything the UI needs.** `agent_runs.transcript`
  holds every tool call with its inputs and outputs, plus the final reasoning. Live
  runs persist it; only eval runs roll back. The observability requirement is largely
  a rendering job, not a capture job.
- **`POST /extract` is stateless** — takes a PDF, returns fields, persists nothing. It
  predates the schema. This is why the current UI appears to have no memory, and it is
  the endpoint that becomes `POST /invoices` rather than being duplicated.

---

## Data model

One migration, three changes.

**`vendors` gains `approval_status` and `created_by`.** `approval_status` is
`pending_approval` or `active`; `created_by` is `agent` or `human`. Existing rows are
backfilled to `active` / `human`. It is `approval_status` rather than `status` because
`Invoice.status` already exists with a different value set, and Phase B joins the two
into one response model. It defaults to `pending_approval`, so a vendor becomes payable
only by an explicit statement, never by omission.

**`invoices` and `purchase_orders` gain `currency`.** Both amounts are currently bare
`Numeric(12,2)`. The eval suite already caught what that costs: case 11 asks the agent
to approve a EUR invoice against a PO with no currency, and the agent correctly refused
because comparing the two as bare numerals makes a reported 0.0% variance meaningless.
That case is unsolvable until the schema can represent the fact it turns on.

**`agent_runs` rows are created at the start of a run**, not the end. See Live
observability below.

---

## Vendor auto-onboarding

Design already recorded in `2026-08-01-invoice-agent-backend.md`; restated here as it
is half of this phase.

The agent **drafts** a vendor, it never creates one. Vendor master file integrity is
the primary fraud control in AP — the classic attack is a fabricated payee whose bank
details are attacker-controlled, and on an unknown invoice the only available source
for those details is the invoice itself. So the agent prepares the record and a human
completes it.

- `lookup_vendor` returning no match lets the agent call a new `draft_vendor` tool,
  which writes a row with `approval_status='pending_approval'` and `created_by='agent'`.
- **The invoice still escalates.** Drafting never unlocks approval, on this invoice or
  any other.
- A human reviews the draft, verifies bank details out of band, and activates it.
- `audit_log` records both the agent's draft and the human's activation.

Two constraints that fail silently if missed:

1. **A pending vendor must not resolve.** `lookup_vendor` matches `approval_status='active'`
   only. If a drafted row counted as a match, the next invoice from that payee would
   resolve cleanly and the control would disappear rather than hold — the fraud window
   simply moves to invoice #2. A pending match returns a distinct third state.
2. **Bank details carried from the invoice are stored unverified.** They are what the
   human is checking; landing in a database row does not make them authoritative.

Eval case 05 keeps expecting `escalate`. That assertion is what protects this control
and must not be relaxed to make the feature pass.

---

## Live observability

The requirement is a ticker showing the agent's latest action while it works, with the
detail view giving the full read afterwards.

**The obstacle.** `RunTranscript.record_tool_call()` appends to an in-memory list, and
`save()` writes the `agent_runs` row once the run has finished. Mid-run there is
nothing to query.

**The change.** Create the `agent_runs` row when the run starts and update its
transcript after each tool call. The frontend polls a light endpoint about once a
second and renders the most recent entry. Tool calls take seconds each, so one-second
polling catches every step and reads as real time.

This is deliberately polling, not streaming. An SSE endpoint would be marginally more
immediate and adds reconnect handling, a second protocol, and more that can fail during
a demo. At this cadence the difference is not perceptible.

Keeping the incremental writes inside `agent_runs.transcript` rather than a separate
events table preserves one source of truth — the transcript is already the single
artifact the dashboard, the audit log, and the eval harness all read.

Labels are rendered client-side from the tool name and input, so the backend stays
dumb: *Resolving vendor "Acme Inc"…*, *Searching policy: "purchase order required
threshold"…*

---

## API

| Endpoint | Purpose |
|---|---|
| `POST /invoices` | Upload a PDF. Persists the invoice, runs extraction and the agent as a background task, returns the id immediately. Evolves `/extract`; does not sit beside it. |
| `GET /invoices` | The queue, newest first, with decision and confidence. |
| `GET /invoices/{id}` | Extracted fields, decision, confidence, reasoning, full tool-call transcript, retrieved policy clauses. |
| `GET /invoices/{id}/activity` | Light poll target: run status and the latest tool call. |
| `POST /invoices/{id}/approve` \| `/reject` | Human decision. Writes `audit_log`. |
| `GET /vendors?approval_status=pending_approval` | Drafted payees awaiting a human. |
| `POST /vendors/{id}/approve` | Activates the vendor. Writes `audit_log`. |

---

## Frontend

Three views, extending the existing Vite/React app rather than replacing it.

**Upload.** Drop a PDF. The invoice appears immediately as pending, with the ticker
above it showing what the agent is doing. Resolves to a decision in place.

**Queue.** Split into *cleared for payment* and *needs you*, because that split is the
product: the straight-through rate is the number an AP team actually cares about. Each
row carries vendor, amount, decision, confidence, and a one-line reason. A badge
surfaces any vendors awaiting approval.

**Detail.** Extracted fields beside the decision and confidence, the full reasoning,
and the tool-call timeline — every call with its inputs and outputs, and any retrieved
policy clauses shown with their section headings so a cited rule can be checked against
the text the agent actually saw. Approve and reject act from here.

---

## Testing

Endpoints get integration tests against the existing `db_session` fixture. The agent is
not re-run in tests — `POST /invoices` dispatches a background task, and tests assert
the row and the queued work rather than paying for a live agent run.

Two existing eval cases interact with this phase and should be re-run once, together,
after it lands rather than piecemeal:

- **case 11** should become solvable once currency exists. It has never passed.
- **case 05** must keep escalating. It is the assertion protecting the vendor control.

Neither is a blocker for building; both cost real money to check, so they wait for one
run at the end.
