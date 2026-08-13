import uuid
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import run_agent
from app.db import SessionLocal, get_session
from app.extraction.pipeline import extract_invoice
from app.models import AgentRun, Invoice, LineItem, Vendor

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

    `invoices` has no vendor name of its own -- only `vendor_id`, which is set
    once a payee resolves to a row in the vendor master. Until then the only
    record of who the invoice claims to be from is the name the agent handed to
    `lookup_vendor`, so that is the fallback. It is what the document said, not
    a verified identity, and an unresolved payee is precisely the case the queue
    most needs to label.
    """
    if vendor is not None:
        return vendor.name
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
