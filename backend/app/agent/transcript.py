import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun


class RunTranscript:
    """The record of one agent review: every tool call, and the final decision.

    Read by three consumers: the dashboard detail view, the audit log, and the
    eval graders. Because the tool runner owns the agent loop, tools record
    themselves here as they execute.

    `decision` doubles as the completion signal -- `submit_recommendation` sets
    it, so `transcript.decision is not None` is how run_agent knows the agent
    has committed.

    The in-memory lists are the source of truth; the row is a projection of
    them. A transcript that is never `begin`-ed still works, since `save`
    inserts the row itself.
    """

    def __init__(self, invoice_id: uuid.UUID, source: str = "live"):
        self.invoice_id = invoice_id
        self.source = source  # "live" | "eval"
        self.tool_calls: list[dict] = []
        self.decision: str | None = None
        self.confidence: float | None = None
        self.reasoning: str | None = None
        self._run: AgentRun | None = None

    async def begin(self, session: AsyncSession) -> AgentRun:
        """Create the run row before the agent starts, so it can be watched."""
        self._run = AgentRun(
            invoice_id=self.invoice_id,
            source=self.source,
            status="running",
            transcript={"tool_calls": [], "reasoning": None},
        )
        session.add(self._run)
        await session.commit()
        await session.refresh(self._run)
        return self._run

    async def record_tool_call(
        self, tool: str, input: dict, output, session: AsyncSession | None = None
    ) -> None:
        self.tool_calls.append({"tool": tool, "input": input, "output": output})
        if session is not None:
            await self._flush(session)

    def record_final(self, decision: str, confidence: float, reasoning: str) -> None:
        self.decision = decision
        self.confidence = confidence
        self.reasoning = reasoning

    def _projection(self) -> dict:
        """A point-in-time copy of the transcript, safe to assign to the row.

        The copy is load-bearing. Assigning a dict that *references*
        `self.tool_calls` looks like a reassignment but is not: the value
        SQLAlchemy holds as "old" is then the same live list the next append
        mutates, so old and new compare equal, the attribute is never marked
        dirty, and no UPDATE is emitted.

        A shallow copy is enough -- entries are built once and never mutated.
        """
        return {"tool_calls": list(self.tool_calls), "reasoning": self.reasoning}

    async def _flush(self, session: AsyncSession) -> None:
        """Republish the in-memory transcript onto the row, if there is one."""
        if self._run is None:
            return
        self._run.transcript = self._projection()
        await session.commit()

    async def save(self, session: AsyncSession) -> AgentRun:
        """Settle the run: write the decision and mark it no longer running."""
        if self._run is None:
            self._run = AgentRun(invoice_id=self.invoice_id, source=self.source, transcript={})
            session.add(self._run)

        self._run.status = "complete"
        self._run.decision = self.decision
        self._run.confidence = self.confidence
        self._run.transcript = self._projection()

        await session.commit()
        await session.refresh(self._run)
        return self._run
