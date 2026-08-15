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

    The in-memory lists are the source of truth; the row is a projection of them.
    `begin` creates that row before the agent starts and each `record_tool_call`
    given a session republishes it, so a review can be watched while it happens
    rather than only read once it is over. A transcript that is never `begin`-ed
    still works -- `save` inserts the row itself -- which is what keeps the eval
    harness and the existing callers unchanged.
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

        The copy is the load-bearing part, not housekeeping. Assigning a dict
        that *references* `self.tool_calls` looks like a reassignment but does
        not behave like one: the value SQLAlchemy holds as "old" is then the
        same live list the next append mutates, so the old and new values
        compare equal, the attribute is never marked dirty, and no UPDATE is
        emitted.

        That failed silently and cost the whole point of writing per call. The
        first flush worked -- `begin` seeds a *different* empty list, so there
        was something to differ from -- and every flush after it was a no-op,
        leaving the ticker frozen on tool call one for the length of a run
        while the agent went on to make six more.

        A shallow copy of the list is enough: each entry is built once in
        `record_tool_call` and never mutated afterwards.
        """
        return {"tool_calls": list(self.tool_calls), "reasoning": self.reasoning}

    async def _flush(self, session: AsyncSession) -> None:
        """Republish the in-memory transcript onto the row, if there is one."""
        if self._run is None:
            # No `begin`, so nothing to update -- `save` will insert the whole
            # transcript at the end. Silently skipping is deliberate: a caller
            # that does not want live visibility should not be made to fail for
            # passing a session.
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
        # Same copy as `_flush`, for the same reason. This one happens to survive
        # without it -- `status` changing is enough to make the row dirty and drag
        # the transcript along -- but relying on a sibling column to carry the
        # write is exactly the accident that hid the bug in `_flush`.
        self._run.transcript = self._projection()

        await session.commit()
        await session.refresh(self._run)
        return self._run
