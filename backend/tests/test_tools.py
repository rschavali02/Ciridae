from datetime import datetime, timedelta, timezone

import pytest

from app.agent.tools import (
    check_duplicate_invoice,
    get_invoice_history,
    get_purchase_order,
    lookup_vendor,
    submit_recommendation,
)
from app.models import Invoice, PurchaseOrder, Vendor


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


# --- check_duplicate_invoice -----------------------------------------------


@pytest.mark.asyncio
async def test_flags_an_exact_duplicate(db_session, seeded_vendor):
    """Same vendor, same amount, same invoice number -- already paid."""
    db_session.add(_paid(seeded_vendor, 500.0, invoice_number="INV-1"))
    await db_session.commit()

    result = await check_duplicate_invoice(
        db_session, vendor_id=str(seeded_vendor.id), amount=500.0, invoice_number="INV-1"
    )

    assert result["match"] == "exact"
    assert result["prior_invoice"]["invoice_number"] == "INV-1"


@pytest.mark.asyncio
async def test_flags_a_near_duplicate_with_a_suffixed_number(db_session, seeded_vendor):
    """A resubmission under 'INV-1-A' is the case an equality check misses
    entirely: filtering on invoice_number = 'INV-1-A' matches nothing, so the
    agent gets no signal at all for a textbook duplicate-submission pattern."""
    db_session.add(_paid(seeded_vendor, 500.0, invoice_number="INV-1"))
    await db_session.commit()

    result = await check_duplicate_invoice(
        db_session, vendor_id=str(seeded_vendor.id), amount=500.0, invoice_number="INV-1-A"
    )

    assert result["match"] == "near"
    assert result["prior_invoice"]["invoice_number"] == "INV-1"


@pytest.mark.asyncio
async def test_flags_a_reused_number_with_a_changed_amount(db_session, seeded_vendor):
    """Same invoice number, different amount -- an altered-invoice pattern."""
    db_session.add(_paid(seeded_vendor, 500.0, invoice_number="INV-1"))
    await db_session.commit()

    result = await check_duplicate_invoice(
        db_session, vendor_id=str(seeded_vendor.id), amount=750.0, invoice_number="INV-1"
    )

    assert result["match"] == "near"


@pytest.mark.asyncio
async def test_reports_none_when_nothing_resembles_it(db_session, seeded_vendor):
    db_session.add(_paid(seeded_vendor, 500.0, invoice_number="INV-1"))
    await db_session.commit()

    result = await check_duplicate_invoice(
        db_session, vendor_id=str(seeded_vendor.id), amount=4200.0, invoice_number="INV-99"
    )

    assert result["match"] == "none"


@pytest.mark.asyncio
async def test_does_not_match_the_invoice_against_itself(db_session, seeded_vendor):
    """The invoice under review is already a row in `invoices`. Without
    excluding it, a vendor+amount query finds itself and every clean invoice
    reports as a duplicate -- rejecting eval case 1 outright."""
    under_review = Invoice(
        vendor_id=seeded_vendor.id,
        amount=5000.0,
        invoice_number="INV-NEW",
        status="pending",
        raw_pdf_path="under_review.pdf",
    )
    db_session.add(under_review)
    await db_session.commit()
    await db_session.refresh(under_review)

    result = await check_duplicate_invoice(
        db_session,
        vendor_id=str(seeded_vendor.id),
        amount=5000.0,
        invoice_number="INV-NEW",
        exclude_invoice_id=under_review.id,
    )

    assert result["match"] == "none"


# --- get_purchase_order ----------------------------------------------------


@pytest.mark.asyncio
async def test_returns_the_purchase_order_amount(db_session):
    db_session.add(PurchaseOrder(po_number="PO-1", amount=6000.0))
    await db_session.commit()

    result = await get_purchase_order(db_session, po_number="PO-1")

    assert result["exists"] is True
    assert result["po_amount"] == pytest.approx(6000.0)


@pytest.mark.asyncio
async def test_reports_a_missing_purchase_order(db_session):
    """Policy II.3: an invoice citing a PO that does not exist must escalate --
    the agent must not assume the PO was raised but not yet recorded."""
    result = await get_purchase_order(db_session, po_number="PO-NOPE")

    assert result["exists"] is False


@pytest.mark.asyncio
async def test_computes_the_variance_in_both_units(db_session):
    """Both units matter because the governing rule is stated in both: a
    discrepancy over 10 percent OR $1,000, whichever is lesser."""
    db_session.add(PurchaseOrder(po_number="PO-2", amount=6000.0))
    await db_session.commit()

    result = await get_purchase_order(db_session, po_number="PO-2", invoice_amount=6400.0)

    assert result["variance_amount"] == pytest.approx(400.0)
    assert result["variance_percent"] == pytest.approx(6.67, abs=0.01)


@pytest.mark.asyncio
async def test_does_not_decide_whether_the_variance_is_acceptable(db_session):
    """The tool reports arithmetic, never the verdict.

    Hard-coding the 10 percent / $1,000 threshold here would let the agent clear
    eval cases 6, 7 and 8 without ever reading the policy -- which would make
    the Phase 5 before/after RAG measurement meaningless, since those cases are
    precisely the ones expected to fail at baseline. It is also the wrong place
    for the rule: a policy change should not require a code change.
    """
    db_session.add(PurchaseOrder(po_number="PO-3", amount=20000.0))
    await db_session.commit()

    result = await get_purchase_order(db_session, po_number="PO-3", invoice_amount=23000.0)

    assert "within_tolerance" not in result
    assert "acceptable" not in result
    assert "verdict" not in result


@pytest.mark.asyncio
async def test_surfaces_the_lesser_of_the_two_case(db_session):
    """Eval case 8: 4 percent but $2,500 over. Inside the percentage limit,
    outside the dollar cap. The tool must report both figures plainly enough
    that an agent holding the rule can catch it."""
    db_session.add(PurchaseOrder(po_number="PO-4", amount=62500.0))
    await db_session.commit()

    result = await get_purchase_order(db_session, po_number="PO-4", invoice_amount=65000.0)

    assert result["variance_amount"] == pytest.approx(2500.0)
    assert result["variance_percent"] == pytest.approx(4.0)


# --- submit_recommendation -------------------------------------------------
#
# Threshold is 0.7 (CONFIDENCE_ESCALATION_THRESHOLD), so 0.9 is confident and
# 0.4 is not.


def test_passes_through_a_confident_decision():
    result = submit_recommendation(decision="approve", confidence=0.9, reasoning="All checks clean.")
    assert result["final_decision"] == "approve"


def test_overrides_a_low_confidence_approval():
    """The agent proposes; the system decides. An unsure approval is the exact
    case human review exists for, so the agent is not permitted to waive it."""
    result = submit_recommendation(decision="approve", confidence=0.4, reasoning="Probably fine.")
    assert result["final_decision"] == "escalate"


def test_overrides_a_low_confidence_rejection_too():
    """Rejection is not the safe default. Wrongly refusing a legitimate invoice
    damages a vendor relationship and stalls a real payment -- an unsure reject
    belongs in front of a human just as much as an unsure approve."""
    result = submit_recommendation(decision="reject", confidence=0.4, reasoning="Feels off.")
    assert result["final_decision"] == "escalate"


def test_leaves_an_explicit_escalation_alone():
    result = submit_recommendation(decision="escalate", confidence=0.3, reasoning="Unclear.")
    assert result["final_decision"] == "escalate"
    assert result["overridden"] is False


def test_records_what_the_agent_actually_said():
    """The audit trail has to show the agent proposed approval and the system
    overrode it -- otherwise an override is indistinguishable from the agent
    having escalated on its own judgment."""
    result = submit_recommendation(decision="approve", confidence=0.4, reasoning="Probably fine.")
    assert result["original_decision"] == "approve"
    assert result["final_decision"] == "escalate"
    assert result["overridden"] is True


def test_escalates_an_unrecognized_decision():
    """The tool runner derives the schema from a plain `str`, so nothing stops
    the model returning 'approved' or 'ok'. An unparseable decision must fail
    toward human review, never toward payment."""
    result = submit_recommendation(decision="approved", confidence=0.95, reasoning="Looks good.")
    assert result["final_decision"] == "escalate"
    assert result["overridden"] is True


def test_escalates_an_out_of_range_confidence():
    """A confidence of 1.4 is not high confidence, it is a broken signal, and a
    broken signal must not be read as clearing the threshold."""
    result = submit_recommendation(decision="approve", confidence=1.4, reasoning="Certain.")
    assert result["final_decision"] == "escalate"
