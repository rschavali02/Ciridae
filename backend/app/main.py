import uuid
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import run_agent
from app.db import SessionLocal, get_session
from app.extraction.pipeline import extract_invoice
from app.models import Invoice, LineItem

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
