import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import require_eval_database_url
from app.db import get_session
from app.main import app
from app.models import Invoice, Vendor

# Tests run against the throwaway eval database, never the application's, because
# the fixtures below empty every table. Pooling is off: an asyncpg connection
# cannot outlive the event loop that opened it, and each test gets its own.
test_engine = create_async_engine(require_eval_database_url(), poolclass=NullPool)


@pytest_asyncio.fixture
async def db_session():
    """A session whose writes are always undone, even after commit()."""
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

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
    """An HTTP client for the app, sharing the test's rolled-back session."""

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
