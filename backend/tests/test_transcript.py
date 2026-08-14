import pytest
from sqlalchemy import select

from app.agent.transcript import RunTranscript
from app.models import AgentRun


@pytest.mark.asyncio
async def test_records_tool_calls_and_final_decision(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id, source="eval")
    await transcript.record_tool_call(
        "lookup_vendor", {"vendor_name": "Acme Inc"}, {"matched": True}
    )
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
    await transcript.record_tool_call("lookup_vendor", {}, {})
    await transcript.record_tool_call("get_invoice_history", {}, {})
    await transcript.record_tool_call("submit_recommendation", {}, {})
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
    await transcript.record_tool_call("lookup_vendor", {}, {"matched": True})
    assert transcript.decision is None
    transcript.record_final(decision="escalate", confidence=0.4, reasoning="Unclear.")
    assert transcript.decision == "escalate"


@pytest.mark.asyncio
async def test_defaults_to_live_source(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    await transcript.save(db_session)
    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert row.source == "live"


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

    await transcript.record_tool_call(
        "lookup_vendor", {"vendor_name": "Acme"}, {"match": "none"}, session=db_session
    )

    row = (await db_session.execute(select(AgentRun))).scalar_one()
    assert [c["tool"] for c in row.transcript["tool_calls"]] == ["lookup_vendor"]


@pytest.mark.asyncio
async def test_a_crash_mid_run_settles_the_row_instead_of_leaving_it_stuck(
    db_session, seeded_invoice, monkeypatch
):
    """The gap the Task 7 review found: the tool runner swallows exceptions
    raised inside a tool call and keeps looping, so a poisoned session (every
    tool shares one for the whole run) degrades every later call the same way
    until whatever finally escapes reaches here uncaught. Before this test, that
    left the row at status="running" forever -- the dashboard ticker would poll
    it indefinitely, and the invoice would match neither the approved nor the
    held bucket on the queue, disappearing from triage entirely. A crash must
    settle the row, not abandon it.
    """
    import app.agent.runner as runner_module

    class _CrashingRunner:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("simulated: DB connection reset mid-tool-call")

    monkeypatch.setattr(
        runner_module.client.beta.messages,
        "tool_runner",
        lambda **kwargs: _CrashingRunner(),
    )

    # Captured before the run: run_agent's rollback expires every object bound
    # to this session, seeded_invoice included, and reading an expired
    # attribute afterwards outside the query's own await triggers a lazy load
    # in a sync context -- a MissingGreenlet error that has nothing to do with
    # the behaviour under test.
    invoice_id = seeded_invoice.id

    with pytest.raises(RuntimeError, match="simulated"):
        await runner_module.run_agent(db_session, seeded_invoice)

    row = (
        await db_session.execute(select(AgentRun).where(AgentRun.invoice_id == invoice_id))
    ).scalar_one()
    assert row.status == "complete"
    assert row.decision == "escalate"
    assert row.confidence == 0.0
    assert "simulated" in row.transcript["reasoning"]
