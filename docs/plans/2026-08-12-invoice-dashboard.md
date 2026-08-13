# Invoice Dashboard and Vendor Onboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upload an invoice, watch the agent work in near real time, and see the outcome — cleared for payment, or held with the reason — with a human approving any new payee the agent drafts.

**Architecture:** Three layers, built bottom-up so each is testable before the next depends on it. Schema and tool changes first (currency, vendor approval status, `draft_vendor`), then the HTTP surface (`/extract` *evolves into* `POST /invoices` — it is not duplicated), then the React views. Live observability comes from creating the `agent_runs` row when a run starts and updating it per tool call, polled about once a second, rather than from a streaming protocol.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Postgres 16 + pgvector, pytest/pytest-asyncio, Vite + React + TypeScript.

**Design doc:** `docs/plans/2026-08-12-invoice-dashboard-design.md`

---

## Context the executing engineer needs

Read these before starting. They explain why several things are shaped the way they are.

- **The database already exists and is populated.** Migrations are at head (`2561082bd6b1`). Do not create schema that already exists — check `\d <table>` first.
- **`POST /extract` is stateless and predates the schema.** Task 8 turns it into `POST /invoices`. Do not leave both.
- **The transcript already records every tool call with inputs and outputs.** `agent_runs.transcript` is the single artifact the dashboard, audit log, and eval harness all read. The UI work is rendering, not capture.
- **The eval harness rolls back every trial**, so live runs persist transcripts and eval runs do not. This is deliberate.
- **`app/agent/tools.py` holds plain functions; `app/agent/runner.py` wraps each in a decorated closure** that binds the session and transcript so Claude never sees them. New tools need both halves.
- **Run tests with `-m "not integration"` for the fast suite.** Integration tests hit real APIs and cost money.

---

## Build order

| Phase | Tasks | Ends with |
|---|---|---|
| A — Schema and tools | 1–7 | Agent can draft vendors, sees currency, and writes its transcript live |
| B — API | 8–12 | Every endpoint the UI needs, tested |
| C — Frontend | 13–17 | Upload, queue, detail, vendor approvals |
| D — Re-measure | 18 | Cases 05 and 11 re-run once, together |

---

## Phase A — Schema and tools

### Task 1: Migration — vendor approval status and currency columns

**Files:**
- Modify: `backend/app/models.py:46-51` (Vendor), `:54-` (Invoice), `:33-43` (PurchaseOrder)
- Create: `backend/alembic/versions/<generated>_vendor_status_and_currency.py`

**Step 1: Extend the models**

```python
# app/models.py -- Vendor
class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    bank_details: Mapped[str] = mapped_column(String, nullable=True)
    # 'pending_approval' | 'active'. A drafted vendor must never resolve in
    # lookup_vendor, or the next invoice from that payee matches cleanly and the
    # control disappears rather than holding.
    #
    # Named `approval_status`, not `status`: `Invoice.status` already exists with
    # a different value set that also has a pending-flavoured member.
    #
    # Python-side default, and the fail-safe way round -- an insert that forgets
    # the column yields a non-payable vendor, not a payable one.
    approval_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending_approval"
    )
    # 'agent' | 'human'. Same value set as `AuditLog.actor`, but that table
    # cannot carry it: `audit_log.invoice_id` is NOT NULL, so it structurally
    # cannot record a vendor creation.
    created_by: Mapped[str] = mapped_column(String, nullable=False, server_default="human")
```

Because the default is Python-side and non-payable, every human-authored insert site must now state `approval_status="active"` explicitly: `fixtures/seed_vendors.py`, the `seeded_vendor` fixture in `tests/conftest.py`, the `ACME` dict in `app/eval/suite.py`, and `scripts/try_agent.py`.

Add to `Invoice` and `PurchaseOrder` (full rationale on `PurchaseOrder.currency`; `Invoice.currency` cross-references it rather than repeating it):

```python
    # ISO 4217. Bare amounts cannot be compared across currencies -- a EUR
    # invoice against a currency-less PO reports a meaningless 0.0% variance.
    # This is why case 11 (11_non_usd_currency_approve) cannot be scored today:
    # §IV.F's local-currency rule cannot be evaluated when nothing records
    # which currency anything is in.
    currency: Mapped[str] = mapped_column(String(3), nullable=True)
```

Both tables also take a `CheckConstraint("currency IS NULL OR currency = upper(currency)")`. `fields.py` is LLM-populated, so `'eur'` and `'EUR'` will both arrive, and two spellings of one currency compare as a mismatch — a false alarm in the exact comparison the column exists to enable.

**Step 2: Generate and inspect the migration**

```bash
cd backend && alembic revision --autogenerate -m "vendor status and currency columns"
```

Open the generated file and hand-edit it. `approval_status` takes `server_default='active'` on the `add_column` so pre-existing rows backfill (NULL would stop them resolving in `lookup_vendor`), then drops it immediately:

```python
op.add_column('vendors', sa.Column('approval_status', sa.String(), server_default='active', nullable=False))
op.alter_column('vendors', 'approval_status', server_default=None)
```

`alembic/env.py` sets neither `compare_type` nor `compare_server_default`, so autogenerate will not detect drift between the model and the migration — keep them in step by hand.

**Step 3: Apply and verify**

```bash
alembic upgrade head
docker compose exec db psql -U invoice_agent -d invoice_agent -c "\d vendors"
docker compose exec db psql -U invoice_agent -d invoice_agent -c "SELECT name, approval_status, created_by FROM vendors;"
```

Expected: every existing vendor reads `active` / `human`, and `\d vendors` shows **no** server default on `approval_status`.

**Step 4: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/
git commit -m "feat: add vendor approval status and currency columns"
```

---

### Task 2: Extraction captures currency

**Files:**
- Modify: `backend/app/extraction/fields.py:15-21` and the `EXTRACT_TOOL` schema at `:24-46`
- Test: `backend/tests/test_fields.py`

**Step 1: Write the failing test**

```python
# tests/test_fields.py
SAMPLE_EUR = """
ACME Incorporated
Invoice #INV-2011
Total: EUR 4,500.00
"""

@pytest.mark.integration
def test_extracts_the_currency():
    result = extract_fields(SAMPLE_EUR)
    assert result.currency == "EUR"


@pytest.mark.integration
def test_defaults_currency_to_none_when_unstated():
    """Absent is not USD. Guessing a currency is exactly the assumption that
    makes a cross-currency comparison look like a match."""
    result = extract_fields("ACME Incorporated\nInvoice #INV-1\nTotal: 500.00")
    assert result.currency in (None, "USD")
```

**Step 2: Run it**

Run: `pytest tests/test_fields.py -v -m integration -k currency`
Expected: FAIL — `ExtractedFields` has no attribute `currency`

**Step 3: Add the field and the schema property**

```python
class ExtractedFields(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    amount: float | None = None
    currency: str | None = None  # ISO 4217, null when the invoice does not say
    due_date: str | None = None
    po_number: str | None = None
    line_items: list[LineItemFields] = []
```

In `EXTRACT_TOOL["input_schema"]["properties"]`, beside `amount`:

```python
            "currency": {
                "type": ["string", "null"],
                "description": (
                    "ISO 4217 code, e.g. USD or EUR. Null if the invoice does not "
                    "state one -- do not infer a default."
                ),
            },
```

**Step 4: Run it**

Run: `pytest tests/test_fields.py -v -m integration -k currency`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/extraction/fields.py backend/tests/test_fields.py
git commit -m "feat: extract invoice currency"
```

---

### Task 3: `get_purchase_order` reports currency

**Files:**
- Modify: `backend/app/agent/tools.py` (`get_purchase_order`)
- Modify: `backend/app/agent/runner.py` (the `get_purchase_order` closure — bind `invoice.currency`)
- Test: `backend/tests/test_tools.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_reports_both_currencies(db_session):
    db_session.add(PurchaseOrder(po_number="PO-9", amount=4500.0, currency="EUR"))
    await db_session.commit()

    result = await get_purchase_order(
        db_session, po_number="PO-9", invoice_amount=4500.0, invoice_currency="EUR"
    )

    assert result["currency_match"] is True
    assert result["po_currency"] == "EUR"


@pytest.mark.asyncio
async def test_withholds_variance_across_currencies(db_session):
    """A 0.0% variance between 4,500 EUR and 4,500 USD is not a match, it is a
    unit error. Reporting it as a match is what let case 11 look approvable."""
    db_session.add(PurchaseOrder(po_number="PO-9", amount=4500.0, currency="USD"))
    await db_session.commit()

    result = await get_purchase_order(
        db_session, po_number="PO-9", invoice_amount=4500.0, invoice_currency="EUR"
    )

    assert result["currency_match"] is False
    assert "variance_percent" not in result
    assert "differ" in result["detail"].lower()
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_tools.py -v -k currenc`
Expected: FAIL — unexpected keyword `invoice_currency`

**Step 3: Implement**

Add the parameter and the guard. Keep the existing behaviour when currencies agree or are both unknown:

```python
async def get_purchase_order(
    session: AsyncSession,
    po_number: str,
    invoice_amount: float | None = None,
    invoice_currency: str | None = None,
) -> dict:
    ...
    po_amount = float(po.amount)
    result = {
        "exists": True,
        "po_number": po.po_number,
        "po_amount": po_amount,
        "po_currency": po.currency,
        "invoice_currency": invoice_currency,
    }

    currencies_known = po.currency is not None and invoice_currency is not None
    result["currency_match"] = (
        po.currency == invoice_currency if currencies_known else None
    )

    if currencies_known and po.currency != invoice_currency:
        # Deliberately no variance. Subtracting figures in different units
        # produces a number that looks authoritative and means nothing.
        result["detail"] = (
            f"Invoice is in {invoice_currency} and the purchase order in "
            f"{po.currency}; the amounts differ in unit and cannot be compared. "
            "No exchange rate is available to this tool."
        )
        return result

    if invoice_amount is not None:
        variance = invoice_amount - po_amount
        result["invoice_amount"] = invoice_amount
        result["variance_amount"] = round(variance, 2)
        result["variance_percent"] = (
            round(abs(variance) / po_amount * 100, 2) if po_amount else None
        )

    return result
```

In `runner.py`, bind the invoice currency the same way the amount is bound:

```python
        out = await tool_impls.get_purchase_order(
            session,
            po_number=po_number,
            invoice_amount=float(invoice.amount) if invoice.amount is not None else None,
            invoice_currency=invoice.currency,
        )
```

**Step 4: Run tests**

Run: `pytest tests/test_tools.py -v -k "currenc or purchase_order"`
Expected: PASS, including the pre-existing purchase order tests.

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/app/agent/runner.py backend/tests/test_tools.py
git commit -m "feat: report currency on purchase order lookups and withhold cross-currency variance"
```

---

### Task 4: `lookup_vendor` matches active vendors only

**Files:**
- Modify: `backend/app/agent/tools.py` (`lookup_vendor`, the SQL at ~`:50-63`)
- Test: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_a_drafted_vendor_does_not_resolve(db_session):
    """The control that makes vendor drafting safe.

    If a pending row counted as a match, the second invoice from a fabricated
    payee would resolve cleanly and approve -- the fraud window would move to
    invoice #2 rather than closing.
    """
    db_session.add(
        Vendor(
            name="Nonesuch Trading LLC",
            normalized_name="nonesuch trading llc",
            approval_status="pending_approval",
            created_by="agent",
        )
    )
    await db_session.commit()

    result = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    assert result["match"] == "drafted"
    assert "awaiting approval" in result["detail"].lower()
```

**Step 2: Run it**

Run: `pytest tests/test_tools.py -v -k drafted`
Expected: FAIL — returns `resolved`

**Step 3: Implement**

Filter the similarity query to `approval_status = 'active'`, then run a second query for pending matches so the agent is told the difference between "nobody by that name" and "already drafted, waiting on a human":

```python
    rows = (
        await session.execute(
            text(
                """
                SELECT id, name, bank_details,
                       similarity(normalized_name, :name) AS sim
                FROM vendors
                WHERE approval_status = 'active'
                  AND similarity(normalized_name, :name) > :threshold
                ORDER BY sim DESC
                LIMIT 2
                """
            ),
            {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
        )
    ).all()

    if not rows:
        pending = (
            await session.execute(
                text(
                    """
                    SELECT name FROM vendors
                    WHERE approval_status = 'pending_approval'
                      AND similarity(normalized_name, :name) > :threshold
                    LIMIT 1
                    """
                ),
                {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
            )
        ).first()
        if pending:
            return {
                "match": "drafted",
                "detail": (
                    f"{vendor_name!r} has already been drafted as a new vendor and is "
                    "awaiting approval. It is not yet payable."
                ),
            }
        return {"match": "none", "detail": f"No vendor on file resembles {vendor_name!r}."}
```

**Step 4: Run the whole tool suite**

Run: `pytest tests/test_tools.py -v -m "not integration"`
Expected: PASS — the existing `lookup_vendor` tests still pass because every vendor they insert states `approval_status="active"`. Nothing defaults to `active`; a vendor built without that keyword is `pending_approval` and will stop resolving the moment this filter lands, so check any new insert site before assuming a failure here is a bug in the filter.

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: restrict vendor resolution to approved vendors"
```

---

### Task 5: `draft_vendor` tool

**Files:**
- Modify: `backend/app/agent/tools.py`
- Test: `backend/tests/test_tools.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_draft_vendor_creates_a_pending_row(db_session):
    result = await draft_vendor(
        db_session,
        vendor_name="Nonesuch Trading LLC",
        bank_details="IBAN GB00NONE00000000000009",
    )

    assert result["status"] == "pending_approval"
    row = (
        await db_session.execute(select(Vendor).where(Vendor.name == "Nonesuch Trading LLC"))
    ).scalar_one()
    assert row.approval_status == "pending_approval"
    assert row.created_by == "agent"


@pytest.mark.asyncio
async def test_draft_vendor_says_it_does_not_authorise_payment(db_session):
    """The tool result is the agent's only signal about what drafting bought it.
    If it reads as success the agent may treat the payee as resolved."""
    result = await draft_vendor(db_session, vendor_name="Nonesuch Trading LLC")
    assert "not" in result["detail"].lower()
    assert result["payable"] is False


@pytest.mark.asyncio
async def test_draft_vendor_is_idempotent(db_session):
    """Three invoices from the same unknown payee should queue one vendor for a
    human, not three."""
    await draft_vendor(db_session, vendor_name="Nonesuch Trading LLC")
    await draft_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    rows = (
        await db_session.execute(select(Vendor).where(Vendor.approval_status == "pending_approval"))
    ).scalars().all()
    assert len(rows) == 1
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_tools.py -v -k draft_vendor`
Expected: FAIL — `draft_vendor` not defined

**Step 3: Implement**

```python
async def draft_vendor(
    session: AsyncSession, vendor_name: str, bank_details: str | None = None
) -> dict:
    """Queue an unknown payee for human approval. Does not make it payable.

    The agent prepares the record; a person completes it. Vendor master file
    integrity is the primary fraud control in accounts payable, and on an
    unknown invoice the only available source for bank details is the invoice
    itself -- which is the document an attacker controls. So the details below
    are stored unverified and the vendor stays unpayable until a human has
    checked them out of band.
    """
    normalized = vendor_name.strip().lower()

    existing = (
        await session.execute(select(Vendor).where(Vendor.normalized_name == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "status": existing.approval_status,
            "payable": existing.approval_status == "active",
            "detail": (
                f"{vendor_name!r} is already on file with status "
                f"{existing.approval_status!r}."
            ),
        }

    session.add(
        Vendor(
            name=vendor_name.strip(),
            normalized_name=normalized,
            bank_details=bank_details,
            approval_status="pending_approval",
            created_by="agent",
        )
    )
    await session.commit()

    return {
        "status": "pending_approval",
        "payable": False,
        "detail": (
            f"{vendor_name!r} has been queued for human approval. Drafting a vendor "
            "does not authorise payment -- this invoice still requires review, and "
            "any bank details taken from the invoice are unverified."
        ),
    }
```

**Step 4: Run tests**

Run: `pytest tests/test_tools.py -v -k draft_vendor`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: add draft_vendor tool that queues an unknown payee for approval"
```

---

### Task 6: Wire `draft_vendor` into the agent

**Files:**
- Modify: `backend/app/agent/runner.py` (`build_tools`, and the returned list)
- Modify: `backend/app/agent/prompts.py`
- Modify: `backend/tests/test_runner.py` (the tool-set assertion)

**Step 1: Update the tool-set test first**

```python
async def test_exposes_the_seven_tools(db_session, seeded_invoice):
    assert set(_tools(db_session, seeded_invoice)) == {
        "lookup_vendor",
        "get_invoice_history",
        "check_duplicate_invoice",
        "get_purchase_order",
        "search_policy",
        "draft_vendor",
        "submit_recommendation",
    }
```

Run: `pytest tests/test_runner.py -v -k seven` → FAIL.

**Step 2: Add the closure**

```python
    @beta_async_tool
    async def draft_vendor(vendor_name: str, bank_details: str | None = None) -> str:
        """Queue an unknown payee for human approval.

        Call this when lookup_vendor finds no vendor on file, so the payee is
        ready for someone to verify. It does not make them payable and does not
        change what you should recommend for this invoice -- an unapproved payee
        still requires human review.

        Args:
            vendor_name: The payee name as printed on the invoice.
            bank_details: Bank details printed on the invoice, if any. These are
                recorded unverified, for a human to check against the vendor.
        """
        args = {"vendor_name": vendor_name, "bank_details": bank_details}
        out = await tool_impls.draft_vendor(session, **args)
        transcript.record_tool_call("draft_vendor", args, out)
        return json.dumps(out)
```

Add `draft_vendor` to the returned list, before `submit_recommendation`.

**Step 3: Add one prompt line**

In `SYSTEM_PROMPT`, after the vendor bullet:

```
- If no vendor is on file for the payee, draft one so a human can approve it. \
Drafting does not make the payee payable and does not change your recommendation.
```

**Step 4: Run tests**

Run: `pytest -m "not integration"`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/agent/runner.py backend/app/agent/prompts.py backend/tests/test_runner.py
git commit -m "feat: wire draft_vendor into the agent loop"
```

---

### Task 7: Persist the transcript as the run happens

**Files:**
- Modify: `backend/app/agent/transcript.py`
- Modify: `backend/app/agent/runner.py` (`run_agent`)
- Test: `backend/tests/test_transcript.py`

This is what makes the live ticker possible. Today the `agent_runs` row is written once, after the run finishes, so nothing is queryable mid-run.

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_the_run_row_exists_before_any_tool_is_called(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    await transcript.begin(db_session)

    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert row.status == "running"
    assert row.decision is None


@pytest.mark.asyncio
async def test_each_tool_call_is_visible_immediately(db_session, seeded_invoice):
    """The ticker polls this row while the agent works. Buffering the calls until
    the end leaves it with nothing to show for the length of the run."""
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    await transcript.begin(db_session)

    await transcript.record_tool_call("lookup_vendor", {"vendor_name": "Acme"}, {"match": "none"}, session=db_session)

    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert [c["tool"] for c in row.transcript["tool_calls"]] == ["lookup_vendor"]
```

**Step 2: Run to verify failure**

Run: `pytest tests/test_transcript.py -v -m "not integration"`
Expected: FAIL — `begin` not defined.

**Step 3: Implement**

Add `status` to `AgentRun` in `models.py` (`'running' | 'complete'`, `server_default='complete'` so existing rows are unaffected) and generate a migration for it. Then:

```python
    async def begin(self, session: AsyncSession) -> AgentRun:
        """Create the run row before the agent starts, so it can be watched."""
        self._run = AgentRun(
            invoice_id=self.invoice_id,
            source=self.source,
            status="running",
            transcript={"tool_calls": [], "reasoning": None},
        )
        session.add(self._run)
        await session.commit()
        await session.refresh(self._run)
        return self._run

    async def _flush(self, session: AsyncSession) -> None:
        # Reassigned rather than mutated: SQLAlchemy does not track in-place
        # changes to a JSONB dict, so appending to it leaves the column stale.
        self._run.transcript = {"tool_calls": self.tool_calls, "reasoning": self.reasoning}
        await session.commit()
```

`record_tool_call` gains an optional `session`; when given, it calls `_flush`. `save()` sets `status='complete'`, writes the decision and confidence, and flushes.

In `run_agent`, call `await transcript.begin(session)` before constructing the runner, and pass `session` through `build_tools` to each `record_tool_call`.

**Step 4: Run tests**

Run: `pytest -m "not integration"`
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/agent/transcript.py backend/app/agent/runner.py backend/app/models.py backend/alembic/versions/ backend/tests/test_transcript.py
git commit -m "feat: write the agent transcript as the run happens"
```

---

## Phase B — API

### Task 8: `POST /invoices` replaces `POST /extract`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_invoices_api.py` (create)

**Step 1: Write the failing test**

```python
def test_upload_persists_an_invoice_and_returns_its_id(client, db_session):
    with open("fixtures/invoices/clean_acme.pdf", "rb") as f:
        response = client.post("/invoices", files={"file": ("clean_acme.pdf", f, "application/pdf")})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert uuid.UUID(body["id"])


def test_extract_endpoint_is_gone(client):
    """One upload path, not two. The stateless endpoint predates the schema."""
    assert client.post("/extract").status_code == 404
```

**Step 2: Run it** → FAIL (404 on `/invoices`).

**Step 3: Implement**

Replace the `/extract` handler. Persist the invoice, then dispatch extraction and the agent via `BackgroundTasks`, returning `202` immediately — the run takes 30–60s and the client polls.

```python
@app.post("/invoices", status_code=202)
async def create_invoice(background: BackgroundTasks, file: UploadFile = File(...)):
    path = save_upload(file)  # writes under fixtures/uploads/, returns the path
    async with SessionLocal() as session:
        invoice = Invoice(raw_pdf_path=path, status="pending")
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)

    background.add_task(process_invoice, invoice.id)
    return {"id": str(invoice.id), "status": invoice.status}
```

`process_invoice` opens its own session, runs `extract_invoice`, writes the fields (including `currency`) onto the row, then calls `run_agent`.

**Step 4: Run tests** → PASS.

**Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_invoices_api.py
git commit -m "feat: evolve /extract into POST /invoices with a background agent run"
```

---

### Task 9: `GET /invoices` and `GET /invoices/{id}`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_invoices_api.py`

**Step 1: Write the failing tests**

```python
def test_lists_invoices_newest_first(client, two_seeded_invoices): ...

def test_detail_includes_the_full_tool_call_transcript(client, decided_invoice):
    body = client.get(f"/invoices/{decided_invoice.id}").json()

    assert body["decision"] == "escalate"
    assert body["reasoning"]
    assert [c["tool"] for c in body["tool_calls"]] == ["lookup_vendor", "submit_recommendation"]
    assert "input" in body["tool_calls"][0] and "output" in body["tool_calls"][0]


def test_detail_surfaces_retrieved_policy_clauses(client, decided_invoice):
    """Pulled out of the transcript rather than left buried in a tool result, so
    the UI can show which provision the decision cited."""
    body = client.get(f"/invoices/{decided_invoice.id}").json()
    assert body["policy_clauses"][0]["section"]
```

**Step 2–4:** Run, implement, run. The detail handler reads the latest `AgentRun` for the invoice and flattens `search_policy` outputs into `policy_clauses`.

**Step 5: Commit**

```bash
git commit -m "feat: add invoice list and detail endpoints"
```

---

### Task 10: `GET /invoices/{id}/activity`

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_invoices_api.py`

The ticker's poll target. Deliberately small — it is fetched once a second.

```python
def test_activity_returns_the_latest_tool_call_while_running(client, running_invoice):
    body = client.get(f"/invoices/{running_invoice.id}/activity").json()

    assert body["status"] == "running"
    assert body["latest"]["tool"] == "get_invoice_history"
    assert body["call_count"] == 2
```

Return `{status, latest: {tool, input} | None, call_count, decision}`. Do **not** return the full transcript here; that is what the detail endpoint is for.

**Commit:** `feat: add a light activity endpoint for the live ticker`

---

### Task 11: Approve and reject

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_invoices_api.py`

```python
def test_approve_sets_status_and_writes_an_audit_row(client, decided_invoice, db_session): ...

def test_audit_row_records_before_and_after(client, decided_invoice, db_session):
    """The audit log is the answer to 'who approved this and what did it look like
    when they did'. Recording only the new state cannot answer the second half."""
    client.post(f"/invoices/{decided_invoice.id}/approve")
    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.actor == "human"
    assert row.before_state["status"] == "escalated"
    assert row.after_state["status"] == "approved"
```

**Commit:** `feat: add human approve and reject endpoints writing to the audit log`

---

### Task 12: Vendor approval endpoints

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_vendors_api.py` (create)

```python
def test_lists_only_pending_vendors(client, drafted_vendor, seeded_vendor): ...

def test_approving_activates_the_vendor(client, drafted_vendor, db_session):
    client.post(f"/vendors/{drafted_vendor.id}/approve")
    ...
    assert row.approval_status == "active"


def test_an_approved_vendor_then_resolves(client, drafted_vendor, db_session):
    """The whole point of the queue: approval is what makes a payee payable, and
    nothing else does."""
    before = await lookup_vendor(db_session, vendor_name=drafted_vendor.name)
    client.post(f"/vendors/{drafted_vendor.id}/approve")
    after = await lookup_vendor(db_session, vendor_name=drafted_vendor.name)

    assert before["match"] == "drafted"
    assert after["match"] == "resolved"
```

**Commit:** `feat: add vendor approval queue endpoints`

---

### Task 12b: End-to-end wiring test

**Files:**
- Create: `backend/tests/test_wiring.py`

Every task in Phase A and B passes its own tests while the features they add can still fail *between* them. The currency chain is the clearest case: Task 1 adds the column, Task 2 makes extraction return the value, Task 3 compares it, and Task 8 writes the row. If Task 8 forgets to copy `fields.currency` onto the invoice, currency is extracted, discarded, and `get_purchase_order` sees `None` forever — silently falling back to the bare-numeral comparison this phase exists to remove. Each task is individually correct and the feature does nothing.

These tests are the gate for that. **They stub the LLM and the agent**, so they cost nothing and run in the fast suite — they check plumbing, not judgement.

**Step 1: Write the tests**

```python
# tests/test_wiring.py
"""Cross-task wiring. Each of these passes only if several tasks agree."""

import pytest
from sqlalchemy import select

from app.agent.tools import get_purchase_order, lookup_vendor
from app.models import Invoice, PurchaseOrder, Vendor


@pytest.mark.asyncio
async def test_a_drafted_vendor_becomes_resolvable_only_after_approval(db_session):
    """Tasks 4, 5 and 12 together. The control is worth nothing if any one of
    the three is right on its own -- drafting must withhold payability, and
    approval must be the only thing that grants it."""
    from app.agent.tools import draft_vendor

    await draft_vendor(db_session, vendor_name="Nonesuch Trading LLC")
    before = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    vendor = (
        await db_session.execute(
            select(Vendor).where(Vendor.normalized_name == "nonesuch trading llc")
        )
    ).scalar_one()
    vendor.approval_status = "active"
    await db_session.commit()

    after = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    assert before["match"] == "drafted"
    assert after["match"] == "resolved"


@pytest.mark.asyncio
async def test_extracted_currency_reaches_the_invoice_row(db_session, monkeypatch):
    """Tasks 1, 2 and 8. Extraction returning a currency is useless if the
    background task drops it on the way to the database."""
    from app.extraction.fields import ExtractedFields
    from app.extraction.pipeline import ExtractionResult
    import app.main as main

    monkeypatch.setattr(
        main,
        "extract_invoice",
        lambda path: ExtractionResult(
            raw_text="ACME Incorporated, EUR 4,500.00",
            fields=ExtractedFields(vendor_name="ACME Incorporated", amount=4500.0, currency="EUR"),
            used_vision_fallback=False,
        ),
    )

    invoice = Invoice(raw_pdf_path="x.pdf", status="pending")
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    await main.apply_extraction(db_session, invoice)

    await db_session.refresh(invoice)
    assert invoice.currency == "EUR"
    assert invoice.amount == 4500.0


@pytest.mark.asyncio
async def test_currency_on_the_row_reaches_the_purchase_order_comparison(db_session):
    """Tasks 1, 3 and 8. The column existing and the tool reading it are
    different things; this fails if the runner does not bind invoice.currency."""
    db_session.add(PurchaseOrder(po_number="PO-5", amount=4500.0, currency="USD"))
    await db_session.commit()

    result = await get_purchase_order(
        db_session, po_number="PO-5", invoice_amount=4500.0, invoice_currency="EUR"
    )

    assert result["currency_match"] is False
    assert "variance_percent" not in result
```

**Step 2: Run them**

Run: `python -m pytest tests/test_wiring.py -v -m "not integration"`

Expected: FAIL initially if any task in the chain is incomplete. The failure message tells you *which* link is broken, which is the point.

**Step 3: Fix whatever is broken**

Do not weaken these tests to make them pass. A failure here means one of tasks 1–12 left a gap — fix the gap. `apply_extraction` in Task 8 must be a separately callable function (not inline in the background task) precisely so this test can reach it without running the agent.

**Step 4: Commit**

```bash
git add backend/tests/test_wiring.py
git commit -m "test: assert the currency and vendor-approval chains work across tasks"
```

---

## Phase C — Frontend

### Task 13: Rewrite `api.ts` against the new endpoints

**Files:**
- Modify: `frontend/src/api.ts`

Replace `extractInvoice` with `uploadInvoice`, `listInvoices`, `getInvoice`, `getActivity`, `approveInvoice`, `rejectInvoice`, `listPendingVendors`, `approveVendor`. Export the types the views need (`InvoiceSummary`, `InvoiceDetail`, `ToolCall`, `Activity`, `PendingVendor`).

**Commit:** `feat: point the frontend API client at the invoice endpoints`

---

### Task 14: Upload view with the live ticker

**Files:**
- Create: `frontend/src/views/Upload.tsx`, `frontend/src/components/AgentTicker.tsx`
- Modify: `frontend/src/App.tsx`

`AgentTicker` polls `/invoices/{id}/activity` on a 1s interval while `status === "running"`, and renders a human label from the tool name and input:

```tsx
const LABELS: Record<string, (input: any) => string> = {
  lookup_vendor: (i) => `Resolving vendor "${i.vendor_name}"…`,
  get_invoice_history: () => "Checking payment history…",
  check_duplicate_invoice: () => "Checking for duplicates…",
  get_purchase_order: (i) => `Looking up ${i.po_number}…`,
  search_policy: (i) => `Searching policy: "${i.query}"…`,
  draft_vendor: (i) => `Drafting new vendor "${i.vendor_name}" for approval…`,
  submit_recommendation: () => "Reaching a decision…",
};
```

Stop polling when status leaves `running`. Clear the interval on unmount — a leaked timer keeps hitting the API after navigation.

**Commit:** `feat: add invoice upload with a live agent activity ticker`

---

### Task 15: Queue view

**Files:**
- Create: `frontend/src/views/Queue.tsx`

Two sections, because that split is the product: **Cleared for payment** (approved) and **Needs you** (escalated or rejected). Each row: vendor, amount with currency, decision, confidence, and a one-line reason. Show the straight-through rate as a count — *"8 of 11 cleared without review"* — since that is the number an AP team actually recognises. A badge links to the vendor queue when any are pending.

**Commit:** `feat: add the invoice queue split by straight-through and held`

---

### Task 16: Detail view

**Files:**
- Create: `frontend/src/views/InvoiceDetail.tsx`, `frontend/src/components/ToolCallTimeline.tsx`

Extracted fields beside decision and confidence, the full reasoning, then the timeline: every call in order with its inputs and outputs, expandable. Retrieved policy clauses render with their section headings so a cited rule can be checked against the text the agent actually saw. Approve and reject act from here.

**Commit:** `feat: add invoice detail with the full tool-call timeline`

---

### Task 17: Vendor approval view

**Files:**
- Create: `frontend/src/views/VendorApprovals.tsx`

Pending vendors with the bank details taken from the invoice, **labelled unverified**, and an approve button. State plainly on the page that approving makes the payee payable on all future invoices — the copy is part of the control, not decoration.

**Commit:** `feat: add the vendor approval queue view`

---

## Phase D — Re-measure

### Task 18: Re-run cases 05 and 11

Once, together, at the end. Both cost real money; neither blocks the build.

```bash
cd backend && PYTHONPATH=. python scripts/probe_case.py 11 3
cd backend && PYTHONPATH=. python scripts/probe_case.py 05 3
```

- **Case 11** should now be solvable: the invoice and PO both carry a currency, so the agent can see they match rather than refusing to compare bare numerals. If it passes, update `finalResults.md` — it moves from "broken task" to "fixed by a schema change the eval suite identified".
- **Case 05** must still escalate. It is the assertion protecting the vendor control, and `draft_vendor` gives the agent a new action on exactly that case. If it now approves, the control has been undermined and Task 5 or 6 is wrong.

Do not tune anything to make case 11 pass. If it still fails, read the transcript and record why.

**Commit:** `docs: re-measure cases 05 and 11 after the currency and vendor changes`
