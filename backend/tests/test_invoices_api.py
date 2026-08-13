"""The upload endpoint: one invoice in, one persisted row and one queued run out."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

import app.main as main
from app.models import Invoice

CLEAN_ACME = "fixtures/invoices/clean_acme.pdf"


@pytest.fixture(autouse=True)
def clean_uploads():
    """Delete whatever these tests upload.

    The endpoint deliberately keeps the PDF -- it is what `raw_pdf_path` points
    at -- so without this every run of the suite leaves another copy of
    clean_acme.pdf under fixtures/uploads.
    """
    before = set(main.UPLOAD_DIR.glob("*")) if main.UPLOAD_DIR.exists() else set()
    yield
    if main.UPLOAD_DIR.exists():
        for path in set(main.UPLOAD_DIR.glob("*")) - before:
            path.unlink()


@pytest.fixture
def queued_runs(monkeypatch):
    """Stand in for the background run, and record what was handed to it.

    `process_invoice` extracts with an LLM and then runs the agent -- two paid
    calls taking 30-60s between them. The ASGI transport runs background tasks
    inline, before the response comes back, so leaving the real one in place
    would bill a live API on every run of the fast suite. What is under test
    here is that the upload is persisted and queued, not what the agent decides.
    """
    invoice_ids: list[uuid.UUID] = []

    async def fake_process_invoice(invoice_id: uuid.UUID) -> None:
        invoice_ids.append(invoice_id)

    monkeypatch.setattr(main, "process_invoice", fake_process_invoice)
    return invoice_ids


@pytest.mark.asyncio
async def test_upload_persists_an_invoice_and_returns_its_id(client, db_session, queued_runs):
    with open(CLEAN_ACME, "rb") as f:
        response = await client.post(
            "/invoices", files={"file": ("clean_acme.pdf", f, "application/pdf")}
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert uuid.UUID(body["id"])

    row = (await db_session.execute(select(Invoice))).scalar_one()
    assert str(row.id) == body["id"]


@pytest.mark.asyncio
async def test_upload_queues_the_invoice_for_review(client, queued_runs):
    """202 is a promise that the work will happen, not that it has. If the
    upload does not dispatch the run, the invoice sits at `pending` forever and
    the endpoint still looks like it succeeded."""
    with open(CLEAN_ACME, "rb") as f:
        response = await client.post(
            "/invoices", files={"file": ("clean_acme.pdf", f, "application/pdf")}
        )

    assert queued_runs == [uuid.UUID(response.json()["id"])]


@pytest.mark.asyncio
async def test_upload_keeps_the_pdf_where_the_row_says_it_is(client, db_session, queued_runs):
    """`raw_pdf_path` is the only route back to the document a decision was made
    from. The background run re-opens the file by that path, so a row pointing at
    bytes that are not there fails after the client has been told 202."""
    original = Path(CLEAN_ACME).read_bytes()
    with open(CLEAN_ACME, "rb") as f:
        await client.post("/invoices", files={"file": ("clean_acme.pdf", f, "application/pdf")})

    row = (await db_session.execute(select(Invoice))).scalar_one()
    assert Path(row.raw_pdf_path).read_bytes() == original


@pytest.mark.asyncio
async def test_extract_endpoint_is_gone(client):
    """One upload path, not two. The stateless endpoint predates the schema."""
    response = await client.post("/extract")
    assert response.status_code == 404
