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
- **Three approved past invoices for Stark Industries** ($9,200 / $9,650 /
  $10,100), so `get_invoice_history` doesn't come back empty on a first-time
  payee — a real first upload with zero history is a legitimate reason for
  the agent to hesitate, and this demo isn't about that

The policy corpus (`documents`, 25 chunks) is untouched — re-embedding it
costs real Voyage API calls for no reason before a demo.

**Verify before presenting:**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c \
  "SELECT name, approval_status FROM vendors ORDER BY name;"
```

Expect exactly three rows, all `active`. If you see anything else (an old
`Hooli Systems`, extra vendors from earlier testing), the reset didn't run
against the DB you think it did — check `DATABASE_URL`.

## 3. Browser

- [ ] Fresh tab at `http://localhost:5173` — reloaded *after* the reset, so
      the Invoices/History tabs don't show stale cached rows
- [ ] Invoices tab: empty, idle upload screen
- [ ] History tab: "No decisions recorded yet."

## 4. Scenario order

**Naming note:** the Stark Industries file is `fixtures/invoices/invoice_05.pdf`.
That number has nothing to do with eval case `05_vendor_not_on_file_escalate`,
which tests the opposite outcome (unknown vendor → escalate). Don't refer to
it as "invoice 5" near anyone who's seen the eval suite — say "the Stark
Industries invoice."

1. **Upload `invoice_05.pdf` (Stark Industries).** Vendor resolves, history
   reads as normal, no PO referenced, no duplicate → should clear on its own.
   Watch the ticker, then open it from the queue to show the reasoning and
   tool-call timeline.
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
