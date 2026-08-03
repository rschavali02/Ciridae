import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun


class RunTranscript:
    """The record of one agent review: every tool call, and the final decision.

    This is the single artifact three consumers read: the dashboard detail view,
    the audit log, and the eval harness graders. Because the tool runner owns the
    agent loop, tools record themselves here as they execute rather than being
    recorded by a loop we control -- see `build_tools` in app/agent/runner.py.

    `decision` doubles as the completion signal: `submit_recommendation` sets it
    as a side effect of running, so `transcript.decision is not None` is how
    run_agent knows the agent has committed to an answer.
    """

    def __init__(self, invoice_id: uuid.UUID, source: str = "live"):
        self.invoice_id = invoice_id
        self.source = source  # "live" | "eval"
        self.tool_calls: list[dict] = []
        self.decision: str | None = None
        self.confidence: float | None = None
        self.reasoning: str | None = None

    def record_tool_call(self, tool: str, input: dict, output) -> None:
        self.tool_calls.append({"tool": tool, "input": input, "output": output})

    def record_final(self, decision: str, confidence: float, reasoning: str) -> None:
        self.decision = decision
        self.confidence = confidence
        self.reasoning = reasoning

    async def save(self, session: AsyncSession) -> AgentRun:
        run = AgentRun(
            invoice_id=self.invoice_id,
            source=self.source,
            transcript={"tool_calls": self.tool_calls, "reasoning": self.reasoning},
            decision=self.decision,
            confidence=self.confidence,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run
