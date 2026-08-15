import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import require_eval_database_url
from app.db import get_session
from app.main import app
from app.models import Invoice, PurchaseOrder, Vendor

# A dedicated test engine with pooling disabled.
#
# pytest-asyncio gives each test its own event loop, but a pooled asyncpg
# connection is bound to the loop that opened it. Reusing the app's module-level
# engine means test 2 checks out a connection created under test 1's now-dead
# loop, and asyncpg fails with "another operation is in progress" -- which looks
# like a transaction bug but is really a loop-lifetime bug. NullPool opens and
# closes a connection per checkout, so nothing outlives its loop.
#
# Pointed at the eval database, not the application's. The fixture below empties
# seven tables -- `documents` among them, which the eval harness pointedly does
# not touch -- and only the outer rollback puts them back. That is a lot of trust
# in one context manager for something that runs on every `pytest` invocation, so
# the blast radius is moved to a database nothing else depends on.
test_engine = create_async_engine(require_eval_database_url(), poolclass=NullPool)


@pytest_asyncio.fixture
async def db_session():
    """A session whose writes are always undone, even after commit().

    Binds the session to an outer transaction and runs it in savepoint mode, so
    code under test can call commit() normally while the outer rollback still
    undoes everything.

    A plain `SessionLocal()` that rolls back after yielding does NOT work here:
    once the code under test commits, there is nothing left to roll back and the
    rows leak into the next test. Test isolation has to survive the commit, not
    assume it never happens.
    """
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        # Match SessionLocal. Without this, commit() expires every loaded object,
        # and the next attribute access triggers a lazy refresh from sync context
        # -- which raises MissingGreenlet rather than reloading.
        expire_on_commit=False,
    )

    # Start from an empty database, not from whatever the last `seed_vendors.py`
    # run left behind. Ambient rows are not neutral: a seeded "ACME Incorporated"
    # sitting alongside the fixture's own copy makes lookup_vendor report an
    # ambiguous match, and the test appears to fail for a reason that has nothing
    # to do with the code. The deletes are inside the outer transaction, so the
    # rollback below restores the real data.
    for table in (
        "agent_runs",
        "audit_log",
        "line_items",
        "documents",
        "invoices",
        "vendors",
        "purchase_orders",
    ):
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def client(db_session):
    """An HTTP client for the app, sharing the test's rolled-back session.

    Driven in-process on the test's own event loop rather than through
    `TestClient`, which runs the app from a second thread with a second loop: the
    session below is bound to an asyncpg connection owned by *this* loop, and
    handing that connection to another one fails the same way pooling does --
    see the note on `test_engine`.

    Overriding `get_session` is what keeps the endpoints' commits inside the
    outer transaction. A handler opening its own `SessionLocal` would write rows
    the rollback cannot reach, and every run would leave invoices behind in the
    development database.
    """

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded_vendor(db_session):
    vendor = Vendor(
        name="ACME Incorporated",
        normalized_name="acme incorporated",
        bank_details="IBAN GB00ACME00000000000001",
        # Stated, not defaulted: the column defaults to `pending_approval` so
        # that nothing becomes payable by omission.
        approval_status="active",
    )
    db_session.add(vendor)
    await db_session.commit()
    await db_session.refresh(vendor)
    return vendor


@pytest_asyncio.fixture
async def seeded_invoice(db_session):
    invoice = Invoice(
        raw_pdf_path="fixtures/invoices/clean_acme.pdf",
        raw_text="Acme Inc invoice, $5,700.00, PO-88213",
        invoice_number="INV-2001",
        amount=5700.00,
        po_number="PO-88213",
        status="pending",
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)
    return invoice
