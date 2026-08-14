"""The invoice endpoints: one invoice in, and the queue and detail views out."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.main as main
from app.models import AgentRun, AuditLog, Invoice

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


@pytest_asyncio.fixture
async def two_seeded_invoices(db_session):
    """Two invoices with explicitly different creation times.

    `created_at` defaults to `now()`, which in Postgres is the *transaction*
    start time -- and the fixture writes both rows in one transaction, so
    letting it default gives the two invoices identical timestamps and the
    ordering assertion below would pass or fail at random.
    """
    now = datetime.now(timezone.utc)
    older = Invoice(
        raw_pdf_path="fixtures/invoices/older.pdf",
        invoice_number="INV-OLD",
        amount=100.00,
        currency="USD",
        status="pending",
        created_at=now - timedelta(hours=1),
    )
    newer = Invoice(
        raw_pdf_path="fixtures/invoices/newer.pdf",
        invoice_number="INV-NEW",
        amount=200.00,
        currency="EUR",
        status="pending",
        created_at=now,
    )
    db_session.add_all([older, newer])
    await db_session.commit()
    return older, newer


@pytest_asyncio.fixture
async def decided_invoice(db_session):
    """An invoice the agent has already finished reviewing.

    The transcript is written by hand rather than by running the agent: what is
    under test is that the endpoint surfaces a transcript, not that a live model
    produces one.
    """
    invoice = Invoice(
        raw_pdf_path="fixtures/invoices/nonesuch.pdf",
        raw_text="Nonesuch Trading LLC invoice, $18,400.00",
        invoice_number="INV-3001",
        amount=18400.00,
        currency="USD",
        status="pending",
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    db_session.add(
        AgentRun(
            invoice_id=invoice.id,
            source="live",
            status="complete",
            decision="escalate",
            confidence=0.55,
            transcript={
                "tool_calls": [
                    {
                        "tool": "lookup_vendor",
                        "input": {"vendor_name": "Nonesuch Trading LLC"},
                        "output": {"match": "none", "detail": "No vendor on file."},
                    },
                    {
                        "tool": "search_policy",
                        "input": {"query": "unknown vendor"},
                        "output": {
                            "clauses": [
                                {
                                    "section": "IV. Vendor Management",
                                    "text": "New payees require verification.",
                                },
                                {
                                    "section": "II. Policy",
                                    "text": "Invoices over $10,000 require review.",
                                },
                            ]
                        },
                    },
                    {
                        "tool": "submit_recommendation",
                        "input": {
                            "decision": "escalate",
                            "confidence": 0.55,
                            "reasoning": "No vendor on file for the payee.",
                        },
                        "output": {"final_decision": "escalate", "overridden": False},
                    },
                ],
                "reasoning": "No vendor on file for the payee.",
            },
        )
    )
    await db_session.commit()
    return invoice


@pytest.mark.asyncio
async def test_lists_invoices_newest_first(client, two_seeded_invoices):
    """The queue is read top-down, so the invoice someone just uploaded has to
    be the one they see first."""
    response = await client.get("/invoices")

    assert response.status_code == 200
    assert [row["invoice_number"] for row in response.json()] == ["INV-NEW", "INV-OLD"]


@pytest.mark.asyncio
async def test_list_carries_the_decision_and_its_one_line_reason(client, decided_invoice):
    """The queue splits on the decision, so a list without it cannot be split.
    The reason comes along because a held invoice with no stated cause tells the
    person looking at it nothing about what to do next."""
    row = (await client.get("/invoices")).json()[0]

    assert row["decision"] == "escalate"
    assert row["confidence"] == 0.55
    assert row["reasoning"] == "No vendor on file for the payee."
    assert row["amount"] == 18400.00
    assert row["currency"] == "USD"


@pytest.mark.asyncio
async def test_list_reports_an_undecided_invoice_as_such(client, two_seeded_invoices):
    """An invoice whose run has not finished has no decision. Reporting one
    anyway -- or omitting the key -- makes the queue lie about what has been
    reviewed."""
    row = (await client.get("/invoices")).json()[0]

    assert row["decision"] is None
    assert row["run_status"] is None


@pytest.mark.asyncio
async def test_list_labels_the_row_with_the_payee_the_agent_looked_up(client, decided_invoice):
    """`invoices` carries no vendor name, and `vendor_id` is null while a payee
    is unresolved -- which is exactly the invoice a queue must not render as a
    blank row."""
    row = (await client.get("/invoices")).json()[0]

    assert row["vendor_name"] == "Nonesuch Trading LLC"


@pytest.mark.asyncio
async def test_detail_includes_the_full_tool_call_transcript(client, decided_invoice):
    body = (await client.get(f"/invoices/{decided_invoice.id}")).json()

    assert body["decision"] == "escalate"
    assert body["reasoning"]
    assert [c["tool"] for c in body["tool_calls"]] == [
        "lookup_vendor",
        "search_policy",
        "submit_recommendation",
    ]
    assert "input" in body["tool_calls"][0] and "output" in body["tool_calls"][0]


@pytest.mark.asyncio
async def test_detail_surfaces_retrieved_policy_clauses(client, decided_invoice):
    """Pulled out of the transcript rather than left buried in a tool result, so
    the UI can show which provision the decision cited."""
    body = (await client.get(f"/invoices/{decided_invoice.id}")).json()

    assert body["policy_clauses"][0]["section"] == "IV. Vendor Management"
    assert body["policy_clauses"][0]["text"]
    assert len(body["policy_clauses"]) == 2


@pytest.mark.asyncio
async def test_detail_of_an_unreviewed_invoice_is_not_an_error(client, seeded_invoice):
    """An invoice is uploaded before it is reviewed, and the detail page is
    where the ticker watches it happen. 404-ing until a run exists would make
    the page unreachable for exactly the window it is there to cover."""
    response = await client.get(f"/invoices/{seeded_invoice.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] is None
    assert body["tool_calls"] == []
    assert body["policy_clauses"] == []


@pytest.mark.asyncio
async def test_detail_of_an_unknown_invoice_is_a_404(client):
    response = await client.get(f"/invoices/{uuid.uuid4()}")

    assert response.status_code == 404


@pytest_asyncio.fixture
async def running_invoice(db_session):
    """An invoice whose review is still in progress.

    Two calls recorded and no decision yet -- the state the transcript is left in
    between `begin()` and `save()`, which is the whole window the ticker exists
    to cover.
    """
    invoice = Invoice(
        raw_pdf_path="fixtures/invoices/clean_acme.pdf",
        invoice_number="INV-2001",
        amount=5700.00,
        currency="USD",
        status="pending",
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    db_session.add(
        AgentRun(
            invoice_id=invoice.id,
            source="live",
            status="running",
            transcript={
                "tool_calls": [
                    {
                        "tool": "lookup_vendor",
                        "input": {"vendor_name": "ACME Incorporated"},
                        "output": {"match": "resolved"},
                    },
                    {
                        "tool": "get_invoice_history",
                        "input": {"vendor_name": "ACME Incorporated"},
                        "output": {"invoice_count": 4},
                    },
                ],
                "reasoning": None,
            },
        )
    )
    await db_session.commit()
    return invoice


@pytest.mark.asyncio
async def test_activity_returns_the_latest_tool_call_while_running(client, running_invoice):
    body = (await client.get(f"/invoices/{running_invoice.id}/activity")).json()

    assert body["status"] == "running"
    assert body["latest"]["tool"] == "get_invoice_history"
    assert body["latest"]["input"] == {"vendor_name": "ACME Incorporated"}
    assert body["call_count"] == 2
    assert body["decision"] is None


@pytest.mark.asyncio
async def test_activity_does_not_carry_the_whole_transcript(client, running_invoice):
    """This is fetched once a second for the length of a run. Sending every call
    with its outputs each time re-sends the entire review to say one thing has
    changed -- and that is what the detail endpoint is already for."""
    body = (await client.get(f"/invoices/{running_invoice.id}/activity")).json()

    assert "tool_calls" not in body
    assert "output" not in body["latest"]


@pytest.mark.asyncio
async def test_activity_reports_the_decision_once_the_run_is_done(client, decided_invoice):
    """The ticker stops on this. Without the decision here the poller has no way
    to tell a finished review from a stalled one except by giving up."""
    body = (await client.get(f"/invoices/{decided_invoice.id}/activity")).json()

    assert body["status"] == "complete"
    assert body["decision"] == "escalate"
    assert body["latest"]["tool"] == "submit_recommendation"


@pytest.mark.asyncio
async def test_activity_before_the_run_starts_is_not_an_error(client, seeded_invoice):
    """The client begins polling the moment it has an id, which is before the
    background task has written a run row. A 404 in that window makes the ticker
    fail on every upload it is meant to narrate."""
    response = await client.get(f"/invoices/{seeded_invoice.id}/activity")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is None
    assert body["latest"] is None
    assert body["call_count"] == 0
    assert body["decision"] is None


@pytest.mark.asyncio
async def test_activity_for_an_unknown_invoice_is_a_404(client):
    response = await client.get(f"/invoices/{uuid.uuid4()}/activity")

    assert response.status_code == 404


@pytest_asyncio.fixture
async def escalated_invoice(db_session, decided_invoice):
    """A reviewed invoice that the agent held for a person to settle.

    Built on `decided_invoice` rather than replacing it: the run there already
    escalates, and `escalated` is the invoice-side state that decision leaves
    behind -- the queue's "needs you" side, and the only state from which a human
    approval is a real change rather than a no-op.
    """
    decided_invoice.status = "escalated"
    await db_session.commit()
    await db_session.refresh(decided_invoice)
    return decided_invoice


@pytest.mark.asyncio
async def test_approve_sets_status_and_writes_an_audit_row(
    client, escalated_invoice, db_session
):
    response = await client.post(f"/invoices/{escalated_invoice.id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    await db_session.refresh(escalated_invoice)
    assert escalated_invoice.status == "approved"

    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.invoice_id == escalated_invoice.id
    assert row.action == "approve"


@pytest.mark.asyncio
async def test_audit_row_records_before_and_after(client, escalated_invoice, db_session):
    """The audit log is the answer to 'who approved this and what did it look like
    when they did'. Recording only the new state cannot answer the second half."""
    await client.post(f"/invoices/{escalated_invoice.id}/approve")

    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.actor == "human"
    assert row.before_state["status"] == "escalated"
    assert row.after_state["status"] == "approved"


@pytest.mark.asyncio
async def test_the_audit_row_keeps_what_the_agent_recommended(
    client, escalated_invoice, db_session
):
    """A human approval is only legible against what it overrode. Without the
    agent's own call on the row, the log records that someone approved an invoice
    but not that they approved one the agent had held."""
    await client.post(f"/invoices/{escalated_invoice.id}/approve")

    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.before_state["decision"] == "escalate"
    assert row.before_state["confidence"] == 0.55
    assert row.before_state["amount"] == 18400.00


@pytest.mark.asyncio
async def test_reject_sets_status_and_writes_an_audit_row(
    client, escalated_invoice, db_session
):
    response = await client.post(f"/invoices/{escalated_invoice.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    await db_session.refresh(escalated_invoice)
    assert escalated_invoice.status == "rejected"

    row = (await db_session.execute(select(AuditLog))).scalar_one()
    assert row.actor == "human"
    assert row.action == "reject"
    assert row.before_state["status"] == "escalated"
    assert row.after_state["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_human_decision_shows_up_in_the_queue(client, escalated_invoice):
    """The point of approving is that the invoice leaves the pile that needs
    attention. If the queue still reads `escalated`, nothing has happened as far
    as the person using it can tell."""
    await client.post(f"/invoices/{escalated_invoice.id}/approve")

    row = (await client.get("/invoices")).json()[0]

    assert row["status"] == "approved"
    # The agent's own call is untouched: a human settling an invoice does not
    # rewrite the review, and the detail view still shows what was overridden.
    assert row["decision"] == "escalate"


@pytest.mark.asyncio
async def test_deciding_an_unknown_invoice_is_a_404(client, db_session):
    """404 rather than a silently-created audit row: `audit_log.invoice_id` is a
    foreign key, so a decision on an id that does not exist has nowhere to be
    recorded and must not look like it succeeded."""
    for action in ("approve", "reject"):
        response = await client.post(f"/invoices/{uuid.uuid4()}/{action}")
        assert response.status_code == 404

    assert (await db_session.execute(select(AuditLog))).scalars().all() == []


@pytest.mark.asyncio
async def test_serves_the_uploaded_pdf(client, db_session, queued_runs):
    """The upload-time preview renders this directly, so it has to be the same
    bytes the agent's extraction ran against -- not a re-fetch, not a copy."""
    with open(CLEAN_ACME, "rb") as f:
        original_bytes = f.read()
        f.seek(0)
        response = await client.post(
            "/invoices", files={"file": ("clean_acme.pdf", f, "application/pdf")}
        )
    invoice_id = response.json()["id"]

    file_response = await client.get(f"/invoices/{invoice_id}/file")

    assert file_response.status_code == 200
    assert file_response.headers["content-type"] == "application/pdf"
    assert file_response.content == original_bytes


@pytest.mark.asyncio
async def test_file_for_an_unknown_invoice_is_a_404(client):
    response = await client.get(f"/invoices/{uuid.uuid4()}/file")
    assert response.status_code == 404
