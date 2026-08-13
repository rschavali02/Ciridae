"""Cross-task wiring. Each of these passes only if several tasks agree."""

import pytest
from sqlalchemy import select

from app.agent.tools import get_purchase_order, lookup_vendor
from app.models import Invoice, PurchaseOrder, Vendor


@pytest.mark.asyncio
async def test_a_drafted_vendor_becomes_resolvable_only_after_approval(db_session):
    """Tasks 4, 5 and 12 together. The control is worth nothing if any one of
    the three is right on its own -- drafting must withhold payability, and
    approval must be the only thing that grants it."""
    from app.agent.tools import draft_vendor

    await draft_vendor(db_session, vendor_name="Nonesuch Trading LLC")
    before = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    vendor = (
        await db_session.execute(
            select(Vendor).where(Vendor.normalized_name == "nonesuch trading llc")
        )
    ).scalar_one()
    vendor.approval_status = "active"
    await db_session.commit()

    after = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")

    assert before["match"] == "drafted"
    assert after["match"] == "resolved"


@pytest.mark.asyncio
async def test_extracted_currency_reaches_the_invoice_row(db_session, monkeypatch):
    """Tasks 1, 2 and 8. Extraction returning a currency is useless if the
    background task drops it on the way to the database."""
    from app.extraction.fields import ExtractedFields
    from app.extraction.pipeline import ExtractionResult
    import app.main as main

    monkeypatch.setattr(
        main,
        "extract_invoice",
        lambda path: ExtractionResult(
            raw_text="ACME Incorporated, EUR 4,500.00",
            fields=ExtractedFields(vendor_name="ACME Incorporated", amount=4500.0, currency="EUR"),
            used_vision_fallback=False,
        ),
    )

    invoice = Invoice(raw_pdf_path="x.pdf", status="pending")
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    await main.apply_extraction(db_session, invoice)

    await db_session.refresh(invoice)
    assert invoice.currency == "EUR"
    assert invoice.amount == 4500.0


@pytest.mark.asyncio
async def test_currency_on_the_row_reaches_the_purchase_order_comparison(db_session):
    """Tasks 1, 3 and 8. The column existing and the tool reading it are
    different things; this fails if the runner does not bind invoice.currency."""
    db_session.add(PurchaseOrder(po_number="PO-5", amount=4500.0, currency="USD"))
    await db_session.commit()

    result = await get_purchase_order(
        db_session, po_number="PO-5", invoice_amount=4500.0, invoice_currency="EUR"
    )

    assert result["currency_match"] is False
    assert "variance_percent" not in result
