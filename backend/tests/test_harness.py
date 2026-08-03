import pytest
from sqlalchemy import func, select

from app.eval.cases import EvalCase
from app.eval.harness import eval_engine, isolated_session, seed_case
from app.models import Invoice, PurchaseOrder, Vendor

ACME = {
    "name": "ACME Incorporated",
    "normalized_name": "acme incorporated",
    "bank_details": "IBAN GB00ACME00000000000001",
}

CASE = EvalCase(
    name="demo",
    vendor=ACME,
    invoice={"amount": 5000.0, "invoice_number": "INV-9", "po_number": "PO-9"},
    past_invoices=[{"amount": 900.0}, {"amount": 1100.0}],
    purchase_order={"po_number": "PO-9", "amount": 5000.0},
    expected_decision="approve",
)


@pytest.mark.asyncio
async def test_seeds_the_world_the_case_describes():
    async with isolated_session() as session:
        invoice = await seed_case(session, CASE)

        assert invoice.status == "pending"
        assert invoice.invoice_number == "INV-9"
        assert (await session.execute(select(func.count(Vendor.id)))).scalar_one() == 1
        assert (await session.execute(select(func.count(PurchaseOrder.id)))).scalar_one() == 1
        # Two historical invoices plus the one under review.
        assert (await session.execute(select(func.count(Invoice.id)))).scalar_one() == 3


@pytest.mark.asyncio
async def test_past_invoices_are_approved_so_history_can_see_them():
    """get_invoice_history counts approved payments only. Seeding history as
    pending would make every vendor look brand new."""
    async with isolated_session() as session:
        await seed_case(session, CASE)
        statuses = (
            await session.execute(select(Invoice.status).where(Invoice.amount.in_([900.0, 1100.0])))
        ).scalars().all()
        assert set(statuses) == {"approved"}


@pytest.mark.asyncio
async def test_a_trial_leaves_no_trace_behind():
    """The isolation guarantee, stated as a test.

    Without it the failure is quiet and misleading: trial 2 sees trial 1's
    vendor, lookup_vendor reports ambiguous instead of resolved, and the case
    fails for a reason unrelated to the agent's judgment.

    Deliberately does not use the db_session fixture. That fixture holds an
    open transaction which has already taken row locks clearing the same
    tables, so isolated_session's own DELETEs block on it and the two sit
    waiting on each other -- a deadlock in the test, not in the harness.
    Verification runs on its own short-lived connection instead.
    """
    models = (Vendor, Invoice, PurchaseOrder)

    async def counts() -> dict:
        async with eval_engine.connect() as conn:
            return {
                m.__name__: (await conn.execute(select(func.count(m.id)))).scalar_one()
                for m in models
            }

    before = await counts()

    async with isolated_session() as session:
        await seed_case(session, CASE)
        # Inside the trial the agent sees only this case's world.
        assert (await session.execute(select(func.count(Vendor.id)))).scalar_one() == 1

    # "Unchanged", not "empty" -- the rollback restores whatever pre-existed
    # rather than wiping the database, which is the property that makes running
    # this against a populated database safe.
    assert await counts() == before


@pytest.mark.asyncio
async def test_repeated_trials_each_start_clean():
    """Two runs in sequence must look identical to the agent -- one vendor and
    two prior invoices each time, not two vendors and four."""
    for _ in range(2):
        async with isolated_session() as session:
            await seed_case(session, CASE)
            assert (await session.execute(select(func.count(Vendor.id)))).scalar_one() == 1
            assert (await session.execute(select(func.count(Invoice.id)))).scalar_one() == 3


@pytest.mark.asyncio
async def test_supports_a_case_with_no_vendor_on_file():
    """Case 5 tests an unknown vendor, so the fixture has to be able to omit one."""
    case = EvalCase(
        name="unknown_vendor",
        vendor=None,
        invoice={"amount": 500.0, "raw_text": "Nonesuch Trading LLC invoice, $500."},
        expected_decision="escalate",
    )
    async with isolated_session() as session:
        invoice = await seed_case(session, case)
        assert invoice.vendor_id is None
        assert (await session.execute(select(func.count(Vendor.id)))).scalar_one() == 0
