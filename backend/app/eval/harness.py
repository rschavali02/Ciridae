"""Runs eval cases against the agent under per-trial isolation.

Isolation is the whole design constraint here. Two of the agent's tools --
get_invoice_history and check_duplicate_invoice -- answer questions *about
accumulated state*, so a trial that leaves rows behind does not merely pollute
the next one, it changes what the next one is testing. Seed the same vendor
twice and lookup_vendor starts reporting an ambiguous match; leave three past
invoices behind and an outlier stops looking like an outlier.

That makes "roll back after each trial" insufficient on its own: run_agent
commits when it saves the transcript, and there is nothing left to roll back
afterwards. Each trial instead runs inside an outer transaction with the
session in savepoint mode, so the trial's own commits are undone with it.

Nothing is persisted, deliberately. An endpoint that wiped or mutated the
invoices table would be a hazard aimed at production data; transcripts are
returned in memory and written to JSON by the caller instead.
"""

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.runner import run_agent
from app.config import settings
from app.eval.cases import CaseResult, EvalCase, TrialResult
from app.models import Invoice, PurchaseOrder, Vendor

# The harness gets its own unpooled engine rather than borrowing the app's.
#
# A pooled asyncpg connection is bound to the event loop that opened it, and the
# harness is driven from several -- a pytest test, a CLI script, a request
# handler. Reusing the app engine across them surfaces as "another operation is
# in progress", which reads like a transaction bug and is really a loop-lifetime
# one. NullPool opens and closes per checkout, so nothing outlives its loop.
#
# The cost is one connection per trial, against a run that makes on the order of
# a hundred model calls. It does not register.
eval_engine = create_async_engine(settings.database_url, poolclass=NullPool)

# Child tables first -- foreign keys point upward.
_TABLES = (
    "agent_runs",
    "audit_log",
    "line_items",
    "invoices",
    "purchase_orders",
    "vendors",
)


@asynccontextmanager
async def isolated_session():
    """A session whose every write is undone, including committed ones."""
    connection = await eval_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        # Start from nothing. Ambient rows are not neutral: a stray vendor with
        # a similar name turns a clean resolution into an ambiguous one, and the
        # case then fails for a reason that has nothing to do with the agent.
        for table in _TABLES:
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


async def seed_case(session: AsyncSession, case: EvalCase) -> Invoice:
    """Build the world this case describes and return the invoice under review."""
    vendor = None
    if case.vendor:
        vendor = Vendor(**case.vendor)
        session.add(vendor)
        await session.flush()

    if case.purchase_order:
        session.add(PurchaseOrder(**case.purchase_order))

    for past in case.past_invoices:
        session.add(
            Invoice(
                vendor_id=vendor.id if vendor else None,
                status="approved",
                raw_pdf_path="historical.pdf",
                **past,
            )
        )

    invoice = Invoice(
        vendor_id=vendor.id if vendor else None,
        status="pending",
        raw_pdf_path="eval_fixture.pdf",
        **case.invoice,
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


async def run_case(case: EvalCase, trials: int = 3) -> CaseResult:
    """Run one case `trials` times, each in a fresh world."""
    results: list[TrialResult] = []

    for _ in range(trials):
        async with isolated_session() as session:
            invoice = await seed_case(session, case)
            transcript = await run_agent(session, invoice, source="eval")
            results.append(
                TrialResult(
                    decision=transcript.decision,
                    confidence=transcript.confidence,
                    tools_called=[c["tool"] for c in transcript.tool_calls],
                    reasoning=transcript.reasoning,
                    tool_calls=transcript.tool_calls,
                )
            )

    return CaseResult(case_name=case.name, trials=results)
