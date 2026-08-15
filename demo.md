# Demo reset checklist

Run this before presenting, and again before any rehearsal that involves
actually clicking through the app — every upload, approve, and reject leaves
real rows behind, and a queue full of yesterday's test data looks bad on
screen and can also change what the agent sees (payment history, duplicate
checks) on the next run.

## 1. Services

- [ ] `docker compose up -d` (from `backend/`) — confirm with `docker compose ps`
- [ ] Backend running: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload`
- [ ] Frontend running: `cd frontend && npm run dev`
- [ ] `curl localhost:8000/health` returns `{"status":"ok"}`

## 2. Reset the data

```bash
cd backend && source venv/bin/activate
python -m fixtures.seed_demo
```

This wipes `agent_runs`, `audit_log`, `line_items`, `invoices`,
`purchase_orders`, and `vendors`, then seeds:

- **Three active vendors**: ACME Incorporated, Globex Corp, Stark Industries
  — all `active`, so `lookup_vendor` resolves cleanly for every seeded/demo
  invoice and `draft_vendor` never fires by accident.
- **Three approved past invoices each for Stark Industries and ACME
  Incorporated** (Stark: $9,200 / $9,650 / $10,100; Acme: $2,600 / $4,200 /
  $5,900), so `get_invoice_history` doesn't come back empty on a first-time
  payee — a real first upload with zero history is a legitimate reason for
  the agent to hesitate, and this demo isn't about that.
- **Two purchase orders**, both sized so §II decides the outcome by
  arithmetic rather than by model judgment:
  - `PO-88213` at $5,000.00, referenced by one of the two Acme invoices
    below — the invoice's $5,700.00 lands **outside** tolerance.
  - `PO-77401` at $9,500.00, referenced by the Stark invoice you upload
    live — its $9,780.10 lands **inside** tolerance.
- **Two already-decided Acme Inc invoices**, pre-seeded complete (not
  live-uploaded) so they're sitting in the Invoices tab the moment you open
  the app:
  - `INV-1000` ($2,805.84, no PO) — **approved**, confidence 93%.
  - `INV-2001` ($5,700.00, references `PO-88213`) — **escalated**, confidence
    88%.

The policy corpus (`documents`, 25 chunks) is untouched — re-embedding it
costs real Voyage API calls for no reason before a demo.

**Why the Acme escalation escalates:** `PO-88213` is on file for $5,000.00.
The invoice totals $5,700.00 — a $700.00 variance, 14.0% over the PO amount.
§II of the AP policy caps PO variance at the *lesser of* 10% or $1,000 USD;
10% of this PO is $500.00, so the $700.00 variance breaches the cap on both
the percentage and dollar basis. The agent's own reasoning (visible by
opening the invoice) cites this exact clause and arithmetic — this is the
"agent proposes, system decides" pattern in miniature: nothing about the
vendor or the amount itself is suspicious, only the unresolved discrepancy
against the PO, which is exactly the kind of thing a human, not the agent,
should clear.

**Why the Stark invoice approves:** the mirror image, and the reason it is
worth uploading live. `PO-77401` is on file for $9,500.00 against a
$9,780.10 invoice — a $280.10 variance, 2.95%. The same §II clause caps this
at the lesser of 10% ($950.00) or $1,000, so the variance clears on both
bases and the agent approves *because it read the rule*, not because no rule
applied. Every other approval in the demo is a no-PO invoice where §II never
bites; this is the only one where a retrieved clause lets a payment through.

Note the document was regenerated to print `PO-77401`. The earlier version
referenced no PO at all, which meant the only PO-shaped question available
was §III's "was a purchase order *required* at this amount?" — and the
policy defers that threshold to the UNFPA Procurement Procedures, a document
not in the corpus. The agent escalated rather than invent a number, which is
correct behaviour and a poor live demo. Same reasoning as eval case 04/09;
see `finalResults.md`.

**Verify before presenting:**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c \
  "SELECT name, approval_status FROM vendors ORDER BY name;"
docker compose exec db psql -U invoice_agent -d invoice_agent -c \
  "SELECT invoice_number, status, decision, confidence FROM invoices i \
   LEFT JOIN agent_runs ar ON ar.invoice_id = i.id ORDER BY invoice_number;"
```

Expect exactly three vendor rows, all `active`. If you see anything else (an
old `Hooli Systems`, extra vendors from earlier testing), the reset didn't
run against the DB you think it did — check `DATABASE_URL`. Expect `INV-1000`
(`approve`) and `INV-2001` (`escalate`) among the invoices, both `pending`
(the agent decided; no human has acted yet).

## 3. Browser

- [ ] Fresh tab at `http://localhost:5173` — reloaded *after* the reset, so
      the Invoices/History tabs don't show stale cached rows
- [ ] Invoices tab: shows the two Acme invoices (one blue/approved row, one
      red/escalated row) — nothing else
- [ ] History tab: "No decisions recorded yet." (neither Acme invoice has
      been acted on by a human yet — that's still available as a live beat,
      see below)

## 4. Scenario order

**Naming note:** the Stark Industries file is `fixtures/invoices/invoice_05.pdf`.
That number has nothing to do with eval case `05_vendor_not_on_file_escalate`,
which tests the opposite outcome (unknown vendor → escalate). Don't refer to
it as "invoice 5" near anyone who's seen the eval suite — say "the Stark
Industries invoice."

0. **Open the two pre-seeded Acme Inc invoices already sitting in the queue**
   — no upload needed, they're there from the reset. This is the fastest way
   to show one queue holding both outcomes side by side:
   - Click `INV-1000` (blue row, approved). Reasoning shows a clean vendor
     resolution, in-range payment history, no PO to reconcile against.
   - Click `INV-2001` (red row, escalated). Reasoning walks through
     `get_purchase_order` finding `PO-88213` at $5,000.00 against a $5,700.00
     invoice, then `search_policy` citing §II's "lesser of 10% or $1,000"
     tolerance — the $700.00/14.0% variance breaches it on both counts. This
     is the concrete "agent proposes, system decides" example: the agent
     surfaces the discrepancy and stops rather than resolving it itself.
1. **Upload `invoice_05.pdf` (Stark Industries).** This is the live beat, and
   it is deliberately paired with `INV-2001` above: **same clause, opposite
   outcome.** The invoice totals $9,780.10 against `PO-77401` at $9,500.00 —
   a $280.10 variance (2.95%), inside §II's cap of the lesser of 10% ($950)
   or $1,000, so it should approve *citing the rule it read*. Watch the
   ticker, then open it from the queue to show the reasoning and tool-call
   timeline.

   Say this part out loud: every other approval in the demo is a no-PO
   invoice where §II simply never applies, so this is the only beat where a
   retrieved clause is what lets a payment *through* rather than blocking
   one. Retrieval is not just a brake.

   **If it escalates anyway**, do not fight it — it is a live model call, not
   a deterministic one. Fall back to narrating the seeded pair, which shows
   the same §II arithmetic with zero risk. Eval case 06 is this exact
   scenario and went 3/3 with retrieval, so the odds are good but not
   guaranteed.
2. **Guardrail beat — pick one:**
   - *Cheap:* upload `invoice_05.pdf` again. `check_duplicate_invoice` should
     catch the exact repeat and the agent should reject or escalate rather
     than pay twice.
   - *Stronger, more setup:* walk through the $40,000 no-PO case from
     `finalResults.md` — approved 3/3 at ~0.81 confidence before RAG, escalated
     3/3 citing the specific policy clause after. This is the best *measured*
     evidence in the project that retrieval wasn't decorative, but it's a
     told story, not a live click-through — decide in advance whether you
     want to reset and re-run it live or just narrate the numbers.

## 5. If something goes wrong live

- **Ticker never updates / stuck on "Starting…":** check the backend
  terminal for a traceback. The agent run may have crashed — per
  `runner.py`'s crash handling it should still settle to `escalate` rather
  than hang forever, but confirm the backend process itself is still alive.
- **Upload 500s:** check `backend/fixtures/uploads/` exists and is writable,
  and that Docker's Postgres container is actually the one `DATABASE_URL`
  points at (`docker compose ps`).
- **Wrong data on screen mid-demo:** don't try to fix it live — `python -m
  fixtures.seed_demo` again and re-upload. It's idempotent; run it as many
  times as you need.
