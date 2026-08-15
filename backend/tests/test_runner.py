import pytest

from app.agent.runner import build_tools, describe_invoice
from app.agent.transcript import RunTranscript
from app.models import Invoice


def _tools(session, invoice):
    return {t.name: t for t in build_tools(session, RunTranscript(invoice_id=invoice.id), invoice)}


@pytest.mark.asyncio
async def test_exposes_the_seven_tools(db_session, seeded_invoice):
    """search_policy joined the set in Phase 5, once the baseline was recorded.

    It was absent through Phase 4 on purpose: the comparison only means
    something if policy access is the single thing that changed between the two
    runs. draft_vendor joined next, so an unresolvable payee has an action
    attached to it rather than only a finding.
    """
    assert set(_tools(db_session, seeded_invoice)) == {
        "lookup_vendor",
        "get_invoice_history",
        "check_duplicate_invoice",
        "get_purchase_order",
        "search_policy",
        "draft_vendor",
        "submit_recommendation",
    }


@pytest.mark.asyncio
async def test_bound_context_never_reaches_the_model_schema(db_session, seeded_invoice):
    """The runner derives each schema from the signature, so anything left as a
    parameter becomes a value Claude is asked to invent. A session or an
    invoice id has no business being model-supplied."""
    leaked = {"session", "transcript", "invoice", "exclude_invoice_id", "invoice_amount"}
    for tool in _tools(db_session, seeded_invoice).values():
        assert not (leaked & set(tool.input_schema.get("properties", {}))), tool.name


@pytest.mark.asyncio
async def test_decision_is_constrained_to_an_enum(db_session, seeded_invoice):
    """Typed as a bare str the model could return 'approved' and slip past the
    schema. Literal puts the constraint back that hand-written JSON gave us."""
    schema = _tools(db_session, seeded_invoice)["submit_recommendation"].input_schema
    assert schema["properties"]["decision"]["enum"] == ["approve", "reject", "escalate"]


@pytest.mark.asyncio
async def test_every_tool_carries_a_description(db_session, seeded_invoice):
    """Docstrings are the API surface here -- an undocumented tool is one the
    model has to guess the purpose of."""
    for tool in _tools(db_session, seeded_invoice).values():
        assert tool.description and len(tool.description) > 40, tool.name


@pytest.mark.asyncio
async def test_calling_a_tool_records_it_on_the_transcript(db_session, seeded_vendor, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, seeded_invoice)}

    await tools["lookup_vendor"].call({"vendor_name": "Acme Inc"})

    assert transcript.tool_calls[0]["tool"] == "lookup_vendor"
    assert transcript.tool_calls[0]["output"]["match"] == "resolved"


@pytest.mark.asyncio
async def test_resolving_a_vendor_writes_it_onto_the_invoice(
    db_session, seeded_vendor, seeded_invoice
):
    """Nothing else sets `invoices.vendor_id`.

    `apply_extraction` writes what the LLM reads off the page, but the page
    carries a vendor name and the id has to be resolved. If this tool does not
    record it, an uploaded invoice keeps vendor_id NULL permanently.
    """
    assert seeded_invoice.vendor_id is None

    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, seeded_invoice)}

    await tools["lookup_vendor"].call({"vendor_name": "Acme Inc"})

    assert seeded_invoice.vendor_id == seeded_vendor.id


@pytest.mark.asyncio
async def test_an_approved_upload_is_caught_when_it_is_resubmitted(db_session, seeded_vendor):
    """The demo failure, end to end: the same invoice uploaded twice, approved
    the first time, and not flagged the second.

    Both checks that guard against paying twice filter on `vendor_id`, so an
    approved invoice carrying NULL is invisible to them -- it is on file, it is
    marked paid, and it still matches nothing. That fails open, and no eval case
    covers it: `seed_case` sets `vendor_id` on the rows it inserts, so the suite
    only ever tests a world where the column is already populated, which is
    precisely the state a live upload never reaches on its own.
    """
    first = Invoice(
        amount=9780.10, invoice_number="INV-1004", status="pending", raw_pdf_path="up.pdf"
    )
    db_session.add(first)
    await db_session.commit()
    await db_session.refresh(first)

    # The agent resolves the payee, exactly as it does on a real upload.
    first_tools = {
        t.name: t for t in build_tools(db_session, RunTranscript(invoice_id=first.id), first)
    }
    await first_tools["lookup_vendor"].call({"vendor_name": "Acme Inc"})

    # A human approves it -- "already paid" is what the duplicate check means.
    first.status = "approved"
    await db_session.commit()

    second = Invoice(
        amount=9780.10, invoice_number="INV-1004", status="pending", raw_pdf_path="up.pdf"
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)

    transcript = RunTranscript(invoice_id=second.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, second)}
    await tools["check_duplicate_invoice"].call(
        {
            "vendor_id": str(seeded_vendor.id),
            "amount": 9780.10,
            "invoice_number": "INV-1004",
        }
    )

    assert transcript.tool_calls[0]["output"]["match"] == "exact"


@pytest.mark.asyncio
async def test_duplicate_check_excludes_the_invoice_under_review(db_session, seeded_vendor):
    """The wrapper binds exclude_invoice_id. Without it the invoice matches
    itself and every clean invoice reports as a duplicate."""
    invoice = Invoice(
        vendor_id=seeded_vendor.id,
        amount=5000.0,
        invoice_number="INV-NEW",
        status="pending",
        raw_pdf_path="under_review.pdf",
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    transcript = RunTranscript(invoice_id=invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, invoice)}

    await tools["check_duplicate_invoice"].call(
        {"vendor_id": str(seeded_vendor.id), "amount": 5000.0, "invoice_number": "INV-NEW"}
    )

    assert transcript.tool_calls[0]["output"]["match"] == "none"


@pytest.mark.asyncio
async def test_submit_recommendation_settles_the_transcript(db_session, seeded_invoice):
    """The runner has no stop_reason hook we control, so run_agent treats a
    recorded decision as the completion signal."""
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, seeded_invoice)}

    assert transcript.decision is None
    await tools["submit_recommendation"].call(
        {"decision": "approve", "confidence": 0.9, "reasoning": "All checks clean."}
    )
    assert transcript.decision == "approve"


@pytest.mark.asyncio
async def test_confidence_floor_applies_through_the_wrapper(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, seeded_invoice)}

    await tools["submit_recommendation"].call(
        {"decision": "approve", "confidence": 0.4, "reasoning": "Probably fine."}
    )

    assert transcript.decision == "escalate"


@pytest.mark.asyncio
async def test_describe_invoice_excludes_orm_internals(seeded_invoice):
    """invoice.__dict__ would ship SQLAlchemy's _sa_instance_state into the
    prompt and silently change what the model sees whenever the schema does."""
    rendered = describe_invoice(seeded_invoice)
    assert "_sa_instance_state" not in rendered
    assert "INV-2001" in rendered
