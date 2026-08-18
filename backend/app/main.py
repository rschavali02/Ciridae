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


UPLOAD_DIR = Path("fixtures/uploads")


@app.get("/health")
async def health():
    return {"status": "ok"}


async def save_upload(file: UploadFile) -> str:
    """Write an uploaded PDF to disk and return the path to it."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "upload.pdf").name
    path = UPLOAD_DIR / f"{uuid.uuid4()}_{filename}"
    path.write_bytes(await file.read())
    return str(path)


def parse_due_date(value: str | None) -> date | None:
    """ISO 8601 or nothing.

    `fields.due_date` is LLM-populated, so a date spelled another way will
    arrive eventually. Dropping it costs one field; raising aborts the review of
    an otherwise readable invoice.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def apply_extraction(session: AsyncSession, invoice: Invoice) -> Invoice:
    """Extract the invoice's fields from its PDF and write them onto its row.

    Separate from `process_invoice` so extraction can be exercised without
    running the agent behind it.
    """
    result = extract_invoice(invoice.raw_pdf_path)
    fields = result.fields

    invoice.raw_text = result.raw_text
    invoice.extracted_vendor_name = fields.vendor_name
    invoice.invoice_number = fields.invoice_number
    invoice.amount = fields.amount
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

    Opens its own session and takes an id rather than the ORM object: the
    request that queued this returned 202 long ago and its session is closed.
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

    Returns as soon as the row exists: a review takes 30-60s, longer than a
    client will hold a connection open. The id handed back is what the dashboard
    then polls.
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

    One query for the whole page rather than one per invoice.

    Most recent, not only: an invoice can be reviewed more than once, and the
    queue should show what the system currently believes.
    """
    if not invoice_ids:
        return {}

    rows = (
        await session.execute(
            select(AgentRun)
            .where(AgentRun.invoice_id.in_(invoice_ids))
            .order_by(AgentRun.created_at.asc(), AgentRun.id.asc())
        )
    ).scalars().all()

    return {run.invoice_id: run for run in rows}


def payee_name(invoice: Invoice, vendor: Vendor | None, run: AgentRun | None) -> str | None:
    """The best available label for who this invoice is from.

    A resolved vendor wins, being a verified identity rather than a claim.
    Failing that, `extracted_vendor_name` is what the document printed -- the
    case the queue most needs to label. The transcript is a last resort, for
    rows written before that column existed.
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
        "currency": invoice.currency,
        "status": invoice.status,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
        "run_status": run.status if run else None,
        "decision": run.decision if run else None,
        "confidence": run.confidence if run else None,
        "reasoning": run.transcript.get("reasoning") if run else None,
    }


def policy_clauses(tool_calls: list[dict]) -> list[dict]:
    """Every policy clause the agent retrieved, in the order it saw them.

    Lifted out of the `search_policy` results so the detail view can show the
    provision a decision cited next to the decision itself.

    Deduplicated on section and text: several searches over one policy routinely
    return the same clause, and listing it three times reads as three rules.
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
    row exists from upload, and this is where its review is watched happening.
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
        "tool_calls": tool_calls,
        "policy_clauses": policy_clauses(tool_calls),
    }


@app.get("/invoices/{invoice_id}/file")
async def get_invoice_file(invoice_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """The invoice's own PDF, for the upload-time preview.

    Served straight from `raw_pdf_path`, so what a reviewer sees is provably the
    same bytes extraction ran against.
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
    """What the agent is doing right now.

    Polled about once a second, so it answers with the one call that changed
    rather than the transcript it belongs to. Outputs are left out of `latest`
    for the same reason: the ticker only renders the tool name and its input.
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

    Carries the agent's own call alongside the invoice's status, because a human
    decision only means something against what it settled or overrode. The
    amount travels with it so the row still says what was cleared if the invoice
    is later corrected.
    """
    return {
        "status": invoice.status,
        "amount": float(invoice.amount) if invoice.amount is not None else None,
        "currency": invoice.currency,
        "decision": run.decision if run else None,
        "confidence": run.confidence if run else None,
    }


class DecisionNote(BaseModel):
    note: str | None = None


async def record_human_decision(
    session: AsyncSession, invoice_id: uuid.UUID, status: str, action: str, note: str | None
) -> dict:
    """Settle an invoice on a person's authority, and record that they did.

    The status change and the audit row are written in one commit: an approved
    invoice with no record of who approved it is the state the log exists to
    make impossible.
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
    """Clear an invoice for payment by human decision.
    """
    note = payload.note if payload else None
    return await record_human_decision(session, invoice_id, "approved", "approve", note)


@app.post("/invoices/{invoice_id}/reject")
async def reject_invoice(
    invoice_id: uuid.UUID,
    payload: DecisionNote | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Refuse an invoice. Records the decision; nothing downstream acts on it."""
    note = payload.note if payload else None
    return await record_human_decision(session, invoice_id, "rejected", "reject", note)


@app.get("/audit-log")
async def list_audit_log(session: AsyncSession = Depends(get_session)):
    """Every human decision, newest first.

    `before_state`/`after_state` carry the agent's decision and confidence at
    that moment, so a reviewer can see what they overrode as well as their own
    reasoning for it.
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
    """Drafted payees waiting on a person to check them out of band."""
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
    """Make a drafted payee payable, once a human has approved them.

    When the agent meets an unknown payee it drafts a vendor, but a drafted
    vendor is not payable and the agent cannot change that. This endpoint is
    where a person, having checked the payee out of band, marks them active --
    and being active is what lets them be paid against at all.
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

    # One commit for the status change and the adoptions: splitting them would
    # allow a vendor to become payable while its invoices stayed orphaned.
    await session.commit()
    await session.refresh(vendor)

    return {
        "id": str(vendor.id),
        "name": vendor.name,
        "approval_status": vendor.approval_status,
        "invoices_linked": adopted,
    }
