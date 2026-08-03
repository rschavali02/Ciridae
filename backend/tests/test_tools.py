import pytest

from app.agent.tools import lookup_vendor
from app.models import Vendor


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
