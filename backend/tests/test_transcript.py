import pytest
from sqlalchemy import select

from app.agent.transcript import RunTranscript
from app.models import AgentRun


@pytest.mark.asyncio
async def test_records_tool_calls_and_final_decision(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id, source="eval")
    transcript.record_tool_call("lookup_vendor", {"vendor_name": "Acme Inc"}, {"matched": True})
    transcript.record_final(decision="approve", confidence=0.9, reasoning="Vendor matched.")
    await transcript.save(db_session)

    row = (
        await db_session.execute(select(AgentRun).where(AgentRun.invoice_id == seeded_invoice.id))
    ).scalar_one()

    assert row.decision == "approve"
    assert row.confidence == 0.9
    assert row.source == "eval"
    assert row.transcript["tool_calls"][0]["tool"] == "lookup_vendor"
    assert row.transcript["tool_calls"][0]["input"] == {"vendor_name": "Acme Inc"}
    assert row.transcript["tool_calls"][0]["output"] == {"matched": True}
    assert row.transcript["reasoning"] == "Vendor matched."


@pytest.mark.asyncio
async def test_preserves_tool_call_order(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    transcript.record_tool_call("lookup_vendor", {}, {})
    transcript.record_tool_call("get_invoice_history", {}, {})
    transcript.record_tool_call("submit_recommendation", {}, {})
    await transcript.save(db_session)

    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert [c["tool"] for c in row.transcript["tool_calls"]] == [
        "lookup_vendor",
        "get_invoice_history",
        "submit_recommendation",
    ]


@pytest.mark.asyncio
async def test_decision_is_none_until_recorded(db_session, seeded_invoice):
    """run_agent uses `transcript.decision is not None` as its completion signal,
    so an un-decided transcript must not look decided."""
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    assert transcript.decision is None
    transcript.record_tool_call("lookup_vendor", {}, {"matched": True})
    assert transcript.decision is None
    transcript.record_final(decision="escalate", confidence=0.4, reasoning="Unclear.")
    assert transcript.decision == "escalate"


@pytest.mark.asyncio
async def test_defaults_to_live_source(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    await transcript.save(db_session)
    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert row.source == "live"
