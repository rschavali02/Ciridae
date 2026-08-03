from datetime import datetime, timedelta, timezone

import pytest

from app.agent.tools import get_invoice_history, lookup_vendor
from app.models import Invoice, Vendor


def _paid(vendor, amount, **kwargs):
    """A previously-approved invoice -- i.e. something this vendor was paid."""
    return Invoice(
        vendor_id=vendor.id,
        amount=amount,
        status="approved",
        raw_pdf_path="historical.pdf",
        **kwargs,
    )


# --- lookup_vendor ---------------------------------------------------------


@pytest.mark.asyncio
async def test_resolves_close_name_variant(db_session, seeded_vendor):
    """The invoice prints 'Acme Inc'; the master record says 'ACME Incorporated'."""
    result = await lookup_vendor(db_session, vendor_name="Acme Inc")
    assert result["match"] == "resolved"
    assert result["vendor_id"] == str(seeded_vendor.id)
    assert result["vendor_name"] == "ACME Incorporated"


@pytest.mark.asyncio
async def test_resolves_short_abbreviation(db_session, seeded_vendor):
    """'Acme' scores 0.278 -- a legitimate abbreviation that a 0.4 threshold
    would reject, escalating an invoice that should have resolved cleanly."""
    result = await lookup_vendor(db_session, vendor_name="Acme")
    assert result["match"] == "resolved"
    assert result["vendor_id"] == str(seeded_vendor.id)


@pytest.mark.asyncio
async def test_returns_none_for_unrelated_name(db_session, seeded_vendor):
    result = await lookup_vendor(db_session, vendor_name="Nonesuch Trading LLC")
    assert result["match"] == "none"


@pytest.mark.asyncio
async def test_flags_ambiguous_match(db_session):
    """Policy IV: a name resolving to more than one record must escalate, so the
    tool has to surface the ambiguity rather than silently picking a winner."""
    for name in ("Acme Industrial Supply", "Acme Industrial Services"):
        db_session.add(Vendor(name=name, normalized_name=name.lower()))
    await db_session.commit()

    result = await lookup_vendor(db_session, vendor_name="Acme Industrial")

    assert result["match"] == "ambiguous"
    assert {c["vendor_name"] for c in result["candidates"]} == {
        "Acme Industrial Supply",
        "Acme Industrial Services",
    }


@pytest.mark.asyncio
async def test_returns_bank_details_for_comparison(db_session, seeded_vendor):
    """The agent compares these against what the invoice prints -- policy IV
    requires escalating any discrepancy."""
    result = await lookup_vendor(db_session, vendor_name="ACME Incorporated")
    assert result["bank_details"] == "IBAN GB00ACME00000000000001"


# --- get_invoice_history ---------------------------------------------------


@pytest.mark.asyncio
async def test_summarizes_a_vendors_payment_history(db_session, seeded_vendor):
    for amount in (900.0, 1000.0, 1100.0):
        db_session.add(_paid(seeded_vendor, amount))
    await db_session.commit()

    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id))

    assert result["count"] == 3
    assert result["average_amount"] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_reports_range_not_just_average(db_session, seeded_vendor):
    """An average alone cannot distinguish a tight spend pattern from a wide
    one, which makes it a weak basis for calling an amount anomalous. A vendor
    averaging 1,733 on invoices of 100/100/5,000 has no 'normal' amount near
    the mean at all. The range says so; the average hides it."""
    for amount in (100.0, 100.0, 5000.0):
        db_session.add(_paid(seeded_vendor, amount))
    await db_session.commit()

    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id))

    assert result["min_amount"] == pytest.approx(100.0)
    assert result["max_amount"] == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_excludes_the_invoice_under_review(db_session, seeded_vendor):
    """History must mean 'what we have already paid'. If the pending invoice
    counted toward its own baseline, a 25,000 outlier against three ~1,000
    invoices would report as 3.6x the average instead of 25x -- quietly
    defusing the exact anomaly the agent is meant to catch."""
    for amount in (900.0, 1000.0, 1100.0):
        db_session.add(_paid(seeded_vendor, amount))
    db_session.add(
        Invoice(
            vendor_id=seeded_vendor.id,
            amount=25000.0,
            status="pending",
            raw_pdf_path="under_review.pdf",
        )
    )
    await db_session.commit()

    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id))

    assert result["count"] == 3
    assert result["average_amount"] == pytest.approx(1000.0)
    assert result["max_amount"] == pytest.approx(1100.0)


@pytest.mark.asyncio
async def test_returns_empty_history_for_a_new_vendor(db_session, seeded_vendor):
    """Nulls, not zeros -- an average of 0.0 would read as 'this vendor
    normally bills nothing', making any invoice look anomalous."""
    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id))

    assert result["count"] == 0
    assert result["average_amount"] is None
    assert result["max_amount"] is None


@pytest.mark.asyncio
async def test_respects_the_lookback_window(db_session, seeded_vendor):
    db_session.add(_paid(seeded_vendor, 500.0))
    db_session.add(
        _paid(
            seeded_vendor,
            99999.0,
            created_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
    )
    await db_session.commit()

    result = await get_invoice_history(
        db_session, vendor_id=str(seeded_vendor.id), lookback_days=365
    )

    assert result["count"] == 1
    assert result["max_amount"] == pytest.approx(500.0)
