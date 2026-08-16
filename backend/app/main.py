import uuid
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import run_agent
from app.db import SessionLocal, get_session
from app.extraction.pipeline import extract_invoice
from app.models import AgentRun, AuditLog, Invoice, LineItem, Vendor

app = FastAPI(title="Invoice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Uploads live beside the fixture invoices rather than in a temp directory:
# `raw_pdf_path` is the only route from a decision back to the document it was
# made from, so the file has to outlive the request that carried it.
UPLOAD_DIR = Path("fixtures/uploads")


@app.get("/health")
async def health():
    return {"status": "ok"}


async def save_upload(file: UploadFile) -> str:
    """Write an uploaded PDF to disk and return the path to it."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Prefixed with a uuid: two uploads named "invoice.pdf" are two different
    # invoices, and the second must not overwrite the document the first was
    # decided from. `Path(...).name` because the filename comes from the client.
    filename = Path(file.filename or "upload.pdf").name
    path = UPLOAD_DIR / f"{uuid.uuid4()}_{filename}"
    path.write_bytes(await file.read())
    return str(path)


def parse_due_date(value: str | None) -> date | None:
    """ISO 8601 or nothing.

    `fields.due_date` is LLM-populated, so a date spelled some other way will
    arrive eventually. Dropping it costs one field; letting it raise aborts the
    whole review of an invoice that is otherwise perfectly readable.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def apply_extraction(session: AsyncSession, invoice: Invoice) -> Invoice:
    """Extract the invoice's fields from its PDF and write them onto its row.

    Kept separate from `process_invoice` so the extraction half can be exercised
    -- and asserted on -- without running the agent behind it.
    """
    result = extract_invoice(invoice.raw_pdf_path)
    fields = result.fields

    invoice.raw_text = result.raw_text
    # Written unconditionally, before anything tries to resolve it. Recording
    # the payee only when `lookup_vendor` succeeds would leave exactly the
    # invoices that need labelling -- unknown payees, ambiguous ones, scans too
    # poor to resolve -- with no record of who they claim to be from.
    invoice.extracted_vendor_name = fields.vendor_name
    invoice.invoice_number = fields.invoice_number
    invoice.amount = fields.amount
    # Upper-cased here rather than trusted: `invoices.currency` is
    # check-constrained to uppercase, and an LLM that returns 'eur' would
    # otherwise fail the insert instead of the invoice simply reading as EUR.
    invoice.currency = fields.currency.upper() if fields.currency else None
    invoice.due_date = parse_due_date(fields.due_date)
    invoice.po_number = fields.po_number

    for item in fields.line_items:
        session.add(
            LineItem(invoice_id=invoice.id, description=item.description, amount=item.amount)
        )

    await session.commit()
    await session.refresh(invoice)
    return invoice


async def process_invoice(invoice_id: uuid.UUID) -> None:
    """Extract and review one uploaded invoice, after the response has gone out.

    Opens its own session: the request that queued this returned 202 long ago
    and its session is closed. Takes an id rather than the ORM object for the
    same reason -- an instance from a closed session cannot be refreshed.
    """
    async with SessionLocal() as session:
        invoice = await session.get(Invoice, invoice_id)
        if invoice is None:
            return
        await apply_extraction(session, invoice)
        await run_agent(session, invoice)


@app.post("/invoices", status_code=202)
async def create_invoice(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Accept an invoice PDF and queue it for review.

    Returns as soon as the row exists rather than waiting on the agent: a review
    takes 30-60s, which is longer than a client will hold a connection open. The
    id handed back is what the dashboard then polls.
    """
    path = await save_upload(file)

    invoice = Invoice(raw_pdf_path=path, status="pending")
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)

    background.add_task(process_invoice, invoice.id)
    return {"id": str(invoice.id), "status": invoice.status}


async def latest_runs(session: AsyncSession, invoice_ids: list[uuid.UUID]) -> dict:
    """Map each invoice id to its most recent agent run.

    One query for the whole page rather than one per invoice: the queue renders
    every invoice with its decision, so a per-row lookup is an N+1 on the one
    endpoint that is loaded most.

    Most recent, not only: an invoice can be reviewed more than once (a resumed
    run, an eval trial that was not rolled back), and the queue should show what
    the system currently believes rather than what it first thought.
    """
    if not invoice_ids:
        return {}

    rows = (
        await session.execute(
            select(AgentRun)
            .where(AgentRun.invoice_id.in_(invoice_ids))
            # Ascending, so the last row written for an invoice is the one left
            # in the dict. `id` breaks the tie because `created_at` defaults to
            # transaction time -- two runs committed together carry the same
            # timestamp and would otherwise order arbitrarily.
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
    ).scalars().all()

    return {run.invoice_id: run for run in rows}


def payee_name(invoice: Invoice, vendor: Vendor | None, run: AgentRun | None) -> str | None:
    """The best available label for who this invoice is from.

    A resolved vendor wins, because that is a verified identity rather than a
    claim. Failing that, `extracted_vendor_name` is what the document printed,
    which is exactly the case the queue most needs to label: an unresolved payee.

    The transcript is still read as a last resort, for rows written before
    `extracted_vendor_name` existed. Those carry no name of their own, and the
    only surviving record of who they claimed to be from is the argument the
    agent passed to `lookup_vendor`.
    """
    if vendor is not None:
        return vendor.name
    if invoice.extracted_vendor_name:
        return invoice.extracted_vendor_name
    if run is None:
        return None
    for call in run.transcript.get("tool_calls", []):
        if call.get("tool") in ("lookup_vendor", "draft_vendor"):
            name = (call.get("input") or {}).get("vendor_name")
            if name:
                return name
    return None


def summarize(invoice: Invoice, vendor: Vendor | None, run: AgentRun | None) -> dict:
    """One queue row: what the invoice is, and where its review got to."""
    return {
        "id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "vendor_name": payee_name(invoice, vendor, run),
        "amount": float(invoice.amount) if invoice.amount is not None else None,
        # Reported beside the amount rather than assumed: a bare 4,500 is a
        # different sum depending on the answer, which is the whole reason the
        # column exists.
        "currency": invoice.currency,
        "status": invoice.status,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        # `run_status` rather than `status`: the invoice has one of its own, and
        # a row carrying two unlabelled statuses invites the reader to conflate
        # "the agent is still working" with "nobody has approved it yet".
        "run_status": run.status if run else None,
        "decision": run.decision if run else None,
        "confidence": run.confidence if run else None,
        "reasoning": run.transcript.get("reasoning") if run else None,
    }


def policy_clauses(tool_calls: list[dict]) -> list[dict]:
    """Every policy clause the agent retrieved, in the order it saw them.

    Lifted out of the `search_policy` results rather than left buried in them so
    the detail view can show the provision a decision cited next to the decision
    itself -- a citation nobody can check against the retrieved text is not much
    of an audit trail.

    Deduplicated on section and text: several searches over one policy routinely
    return the same clause, and listing it three times reads as three separate
    rules.
    """
    seen: set[tuple[str, str]] = set()
    clauses: list[dict] = []
    for call in tool_calls:
        if call.get("tool") != "search_policy":
            continue
        for clause in (call.get("output") or {}).get("clauses", []):
            key = (clause.get("section"), clause.get("text"))
            if key in seen:
                continue
            seen.add(key)
            clauses.append(clause)
    return clauses


@app.get("/invoices")
async def list_invoices(session: AsyncSession = Depends(get_session)):
    """The queue: every invoice, newest first, with how its review turned out."""
    invoices = (
        await session.execute(
            # `id` breaks ties for the same reason it does above -- uploads made
            # inside one transaction share a timestamp.
            select(Invoice).order_by(Invoice.created_at.desc(), Invoice.id.desc())
        )
    ).scalars().all()

    runs = await latest_runs(session, [invoice.id for invoice in invoices])
    vendors = {
        vendor.id: vendor
        for vendor in (
            await session.execute(
                select(Vendor).where(
                    Vendor.id.in_([i.vendor_id for i in invoices if i.vendor_id])
                )
            )
        ).scalars().all()
    }

    return [
        summarize(invoice, vendors.get(invoice.vendor_id), runs.get(invoice.id))
        for invoice in invoices
    ]


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """One invoice, with the whole transcript of how it was decided.

    An invoice with no run yet is a 200 with an empty transcript, not a 404: the
    row exists from the moment it is uploaded, and the detail page is where its
    review is watched happening.
    """
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No invoice {invoice_id}")

    run = (await latest_runs(session, [invoice.id])).get(invoice.id)
    vendor = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    tool_calls = run.transcript.get("tool_calls", []) if run else []

    return {
        **summarize(invoice, vendor, run),
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "po_number": invoice.po_number,
        # The full call sequence, inputs and outputs included. The point of the
        # timeline is that a reviewer can see what the agent was told, not just
        # what it concluded from it.
        "tool_calls": tool_calls,
        "policy_clauses": policy_clauses(tool_calls),
    }


@app.get("/invoices/{invoice_id}/file")
async def get_invoice_file(invoice_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """The invoice's own PDF, for the upload-time preview.

    Serves straight from `raw_pdf_path` rather than proxying through anything
    that re-derives it, so what a reviewer sees while the agent works is
    provably the same bytes the agent's extraction ran against.
    """
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No invoice {invoice_id}")

    path = Path(invoice.raw_pdf_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Invoice file is no longer on disk")

    return FileResponse(path, media_type="application/pdf")


@app.get("/invoices/{invoice_id}/activity")
async def get_activity(invoice_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """What the agent is doing right now -- and nothing else.

    Polled about once a second for the length of a review, so it answers with the
    one call that has changed rather than the transcript it belongs to. Returning
    the whole thing here would re-send the entire review every second to report a
    single new line, and the detail endpoint already serves that.

    Outputs are left out of `latest` for the same reason: a tool result runs to
    kilobytes -- retrieved policy clauses especially -- while the ticker only
    renders a label built from the tool name and its input.

    `status` is the run's, not the invoice's, and is null until the run row
    exists. The gap is real: the upload returns 202 as soon as the row is
    written, and the client starts polling before the background task has begun.
    Null means "not started", which is why that window is a 200 and not a 404 --
    a 404 there would break the ticker on every upload it exists to narrate.
    """
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No invoice {invoice_id}")

    run = (await latest_runs(session, [invoice.id])).get(invoice.id)
    tool_calls = run.transcript.get("tool_calls", []) if run else []
    latest = tool_calls[-1] if tool_calls else None

    return {
        "status": run.status if run else None,
        "latest": (
            {"tool": latest.get("tool"), "input": latest.get("input")} if latest else None
        ),
        "call_count": len(tool_calls),
        "decision": run.decision if run else None,
    }


def audit_state(invoice: Invoice, run: AgentRun | None) -> dict:
    """What the invoice looked like at one moment, for the audit log.

    Carries the agent's own call alongside the invoice's status because a human
    decision only means something against what it settled or overrode: "approved"
    on its own does not record that someone released an invoice the agent had
    held at 0.55 confidence. The amount travels with it so the row still says
    what was cleared if the invoice is later corrected.

    Both `before_state` and `after_state` are written in this shape so the two
    can be read as a diff rather than as two differently-shaped snapshots.
    """
    return {
        "status": invoice.status,
        "amount": float(invoice.amount) if invoice.amount is not None else None,
        "currency": invoice.currency,
        "decision": run.decision if run else None,
        "confidence": run.confidence if run else None,
    }


class DecisionNote(BaseModel):
    # Optional, and a request with no body at all must still work -- the
    # existing tests call approve/reject with nothing attached, and a note is
    # something a reviewer adds, not something the endpoint should require.
    note: str | None = None


async def record_human_decision(
    session: AsyncSession, invoice_id: uuid.UUID, status: str, action: str, note: str | None
) -> dict:
    """Settle an invoice on a person's authority, and record that they did.

    The status change and the audit row are written in one commit: an approved
    invoice with no record of who approved it is precisely the state the log
    exists to make impossible.

    The agent's run is left exactly as it was. A human decision is a separate
    event from the review that preceded it -- overwriting the recommendation
    would erase the disagreement, which is the part worth keeping. The note is
    the reviewer's own words for why -- distinct from that reasoning, not a
    replacement for it.
    """
    invoice = await session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"No invoice {invoice_id}")

    run = (await latest_runs(session, [invoice.id])).get(invoice.id)
    before = audit_state(invoice, run)

    invoice.status = status
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            # The endpoint is only reachable by a person; the agent never calls
            # it. `submit_recommendation` is where the agent's decisions land.
            actor="human",
            action=action,
            before_state=before,
            after_state=audit_state(invoice, run),
            note=note,
        )
    )
    await session.commit()
    await session.refresh(invoice)

    vendor = await session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    return summarize(invoice, vendor, run)


@app.post("/invoices/{invoice_id}/approve")
async def approve_invoice(
    invoice_id: uuid.UUID,
    payload: DecisionNote | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Clear an invoice for payment.

    Deliberately not guarded on the current status. The reviewer is the authority
    here, and an invoice the agent approved can still need a person to confirm
    it; refusing the second approval would only mean the queue and the log
    disagree about what happened. Every call writes its own audit row, so a
    changed mind reads as two decisions rather than as one silently rewritten.
    """
    note = payload.note if payload else None
    return await record_human_decision(session, invoice_id, "approved", "approve", note)


@app.post("/invoices/{invoice_id}/reject")
async def reject_invoice(
    invoice_id: uuid.UUID,
    payload: DecisionNote | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Refuse an invoice. Terminal in the same sense `approved` is -- it records
    the decision; nothing downstream acts on it."""
    note = payload.note if payload else None
    return await record_human_decision(session, invoice_id, "rejected", "reject", note)


@app.get("/audit-log")
async def list_audit_log(session: AsyncSession = Depends(get_session)):
    """Every human decision, newest first -- the "when did someone act, and
    why" view the queue itself does not answer.

    `before_state`/`after_state` already carry the agent's decision and
    confidence at that moment, so a reviewer opening this can see both what
    they overrode (or confirmed) and their own reasoning for it, not just the
    bare action.
    """
    entries = (
        await session.execute(
            select(AuditLog).where(AuditLog.actor == "human").order_by(AuditLog.created_at.desc())
        )
    ).scalars().all()

    invoice_ids = {e.invoice_id for e in entries}
    invoices = {
        invoice.id: invoice
        for invoice in (
            await session.execute(select(Invoice).where(Invoice.id.in_(invoice_ids)))
        ).scalars().all()
    }
    vendor_ids = {inv.vendor_id for inv in invoices.values() if inv.vendor_id is not None}
    vendors = {
        vendor.id: vendor
        for vendor in (
            await session.execute(select(Vendor).where(Vendor.id.in_(vendor_ids)))
        ).scalars().all()
    }
    # Same fallback every other view uses: an invoice whose vendor never
    # resolved has no Vendor row to read a name from, and payee_name() is what
    # falls back to the name the agent tried to look up instead of leaving the
    # row blank -- exactly the case a rejected, unresolved-payee invoice is.
    runs = await latest_runs(session, list(invoice_ids))

    result = []
    for entry in entries:
        invoice = invoices.get(entry.invoice_id)
        vendor = vendors.get(invoice.vendor_id) if invoice and invoice.vendor_id else None
        run = runs.get(entry.invoice_id)
        result.append(
            {
                "id": str(entry.id),
                "invoice_id": str(entry.invoice_id),
                "vendor_name": payee_name(invoice, vendor, run) if invoice else None,
                "amount": float(invoice.amount) if invoice and invoice.amount is not None else None,
                "currency": invoice.currency if invoice else None,
                "action": entry.action,
                "note": entry.note,
                "agent_decision": (entry.before_state or {}).get("decision"),
                "decided_at": entry.created_at.isoformat() if entry.created_at else None,
            }
        )
    return result


@app.get("/vendors/pending")
async def list_pending_vendors(session: AsyncSession = Depends(get_session)):
    """Drafted payees waiting on a person to check them out of band.

    Only the pending ones: an active vendor is already payable and has nothing
    left here for a human to act on.
    """
    vendors = (
        await session.execute(
            select(Vendor)
            .where(Vendor.approval_status == "pending_approval")
            .order_by(Vendor.name)
        )
    ).scalars().all()

    return [
        {"id": str(vendor.id), "name": vendor.name, "bank_details": vendor.bank_details}
        for vendor in vendors
    ]


@app.post("/vendors/{vendor_id}/approve")
async def approve_vendor(vendor_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Make a drafted payee payable, and adopt the invoices that were waiting on
    them.

    Nothing else makes a payee payable -- see `lookup_vendor`, which only
    resolves `active` vendors.

    The back-link is the other half. An invoice that arrived before its vendor
    existed was left with `vendor_id` NULL, because the name resolved to
    nothing at the time. It stays that way for good unless something adopts it,
    and an orphaned invoice is invisible to both checks that filter on the
    vendor: it never counts toward that payee's history, and a resubmission of
    it can never be matched as a duplicate. So a vendor's first invoice would
    silently never exist, and their second would still read as a first-time
    payee.

    Matched on the normalized printed name rather than by similarity. A wrong
    adoption attributes someone else's payment to this vendor and quietly
    corrupts the baseline the anomaly check reads, which is worse than leaving a
    row orphaned. Exact-normalized is the conservative half of that trade: it
    catches the ordinary case, where the draft was created from this very name.
    """
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail=f"No vendor {vendor_id}")

    vendor.approval_status = "active"

    adopted = (
        await session.execute(
            update(Invoice)
            .where(
                Invoice.vendor_id.is_(None),
                func.lower(func.trim(Invoice.extracted_vendor_name)) == vendor.normalized_name,
            )
            .values(vendor_id=vendor.id)
        )
    ).rowcount

    # One commit for the status change and the adoptions together. Splitting
    # them would allow a vendor to become payable while its invoices stayed
    # orphaned, which is the exact state this endpoint exists to clear.
    await session.commit()
    await session.refresh(vendor)

    return {
        "id": str(vendor.id),
        "name": vendor.name,
        "approval_status": vendor.approval_status,
        "invoices_linked": adopted,
    }
