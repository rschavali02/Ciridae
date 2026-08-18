"""Runs eval cases against the agent under per-trial isolation.

Two of the agent's tools answer questions about accumulated state, so a trial
that leaves rows behind changes what the next one is testing. Each trial runs inside 
an outer transaction with the session in savepoint mode,which undoes the trial's own 
commits with it.
"""

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.runner import run_agent
from app.config import require_eval_database_url
from app.eval.cases import CaseResult, EvalCase, TrialResult
from app.models import Invoice, PurchaseOrder, Vendor


eval_engine = create_async_engine(require_eval_database_url(), poolclass=NullPool)

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
