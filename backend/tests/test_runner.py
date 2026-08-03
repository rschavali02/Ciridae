import pytest

from app.agent.runner import build_tools, describe_invoice
from app.agent.transcript import RunTranscript
from app.models import Invoice


def _tools(session, invoice):
    return {t.name: t for t in build_tools(session, RunTranscript(invoice_id=invoice.id), invoice)}


@pytest.mark.asyncio
async def test_exposes_the_five_structured_tools(db_session, seeded_invoice):
    """search_policy is deliberately absent -- Phase 4 measures the agent
    without policy access, and adding it early would erase the baseline."""
    assert set(_tools(db_session, seeded_invoice)) == {
        "lookup_vendor",
        "get_invoice_history",
        "check_duplicate_invoice",
        "get_purchase_order",
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
