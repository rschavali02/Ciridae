import pytest_asyncio
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.models import Invoice, Vendor

# A dedicated test engine with pooling disabled.
#
# pytest-asyncio gives each test its own event loop, but a pooled asyncpg
# connection is bound to the loop that opened it. Reusing the app's module-level
# engine means test 2 checks out a connection created under test 1's now-dead
# loop, and asyncpg fails with "another operation is in progress" -- which looks
# like a transaction bug but is really a loop-lifetime bug. NullPool opens and
# closes a connection per checkout, so nothing outlives its loop.
test_engine = create_async_engine(settings.database_url, poolclass=NullPool)


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
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def seeded_vendor(db_session):
    vendor = Vendor(
        name="ACME Incorporated",
        normalized_name="acme incorporated",
        bank_details="IBAN GB00ACME00000000000001",
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
