"""Plain implementations of the agent's tools.

Ordinary async functions taking a DB session, so they stay unit-testable in
isolation. `app/agent/runner.py` wraps each in a closure binding the session and
transcript, so the schema Claude sees never mentions either.

Each returns a small dict rather than raw rows -- a conclusion the agent can
reason about, not a table it has to summarize.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Invoice, PurchaseOrder, Vendor
from app.rag.search import DEFAULT_TOP_K as POLICY_TOP_K
from app.rag.search import search_policy as rag_search_policy

VALID_DECISIONS = ("approve", "reject", "escalate")

# Measured trigram scores against the seeded vendors:
#   legitimate variants   "acme" 0.278 .. "acme incorporated llc" 0.818
#   unrelated names       0.000 .. 0.071
# 0.2 sits in the empty gap between the bands. Re-measure before trusting this
# against a real vendor master, which has a much fatter false-positive tail.
SIMILARITY_THRESHOLD = 0.2

# If the runner-up is within this of the best match, the name does not identify
# a single vendor and the agent must be told so rather than handed a winner.
AMBIGUITY_MARGIN = 0.10


async def lookup_vendor(session: AsyncSession, vendor_name: str) -> dict:
    """Resolve a printed vendor name against the vendor master file.

    Returns one of four shapes -- resolved, none, ambiguous, drafted -- because
    the agent must act differently on each.

    Only `active` vendors resolve. A drafted vendor matching here would let the
    next invoice from that payee clear cleanly, and the approval control would
    disappear rather than hold.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, name, bank_details,
                       similarity(normalized_name, :name) AS sim
                FROM vendors
                WHERE approval_status = 'active'
                  AND similarity(normalized_name, :name) > :threshold
                ORDER BY sim DESC
                LIMIT 2
                """
            ),
            {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
        )
    ).all()

    if not rows:
        # A separate query so the agent is told the difference between "nobody
        # by that name" and "already drafted, waiting on a human".
        pending = (
            await session.execute(
                text(
                    """
                    SELECT name FROM vendors
                    WHERE approval_status = 'pending_approval'
                      AND similarity(normalized_name, :name) > :threshold
                    LIMIT 1
                    """
                ),
                {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
            )
        ).first()
        if pending:
            # Names the draft that matched, not the name printed on the invoice:
            # the threshold is loose, so reporting the invoice's spelling back
            # would put a false statement into the transcript a human reviews.
            return {
                "match": "drafted",
                "drafted_name": pending.name,
                "detail": (
                    f"{vendor_name!r} resembles {pending.name!r}, which has already been "
                    "drafted as a new vendor and is awaiting approval. It is not yet payable."
                ),
            }
        return {
            "match": "none",
            "detail": f"No vendor on file resembles {vendor_name!r}.",
        }

    best = rows[0]

    if len(rows) > 1 and (best.sim - rows[1].sim) < AMBIGUITY_MARGIN:
        return {
            "match": "ambiguous",
            "detail": f"{vendor_name!r} matches more than one vendor on file.",
            "candidates": [
                {"vendor_name": r.name, "similarity": round(r.sim, 2)} for r in rows
            ],
        }

    # The similarity score is withheld on a resolved match. It is calibrated
    # against a distribution the agent cannot see -- 0.42 is unremarkable for
    # "Acme Inc" vs "ACME Incorporated" but reads as poor on a 0-1 scale, and
    # the agent has cut its confidence over exactly that. Scores are still
    # returned on the ambiguous branch, where they are used comparatively.
    return {
        "match": "resolved",
        "vendor_id": str(best.id),
        "vendor_name": best.name,
        "bank_details": best.bank_details,
    }


async def draft_vendor(
    session: AsyncSession, vendor_name: str, bank_details: str | None = None
) -> dict:
    """Queue an unknown payee for human approval. Does not make it payable.

    On an unknown invoice the only source for bank details is the invoice
    itself, which is the document an attacker controls. So details are stored
    unverified and the vendor stays unpayable until a human checks them out of
    band.
    """
    normalized = vendor_name.strip().lower()

    # Exact normalized match, not trigram similarity: this runs after
    # lookup_vendor already reported no match, and only exists to keep repeat
    # invoices from queuing the same row three times.
    existing = (
        await session.execute(select(Vendor).where(Vendor.normalized_name == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        # Bank details on a repeat invoice are compared, never silently dropped:
        # two invoices from one payee naming different accounts is close to the
        # canonical vendor-fraud tell. The stored value is not overwritten --
        # the first submission is no more trustworthy than the second, and
        # replacing it would destroy the discrepancy rather than surface it.
        conflicting = (
            bank_details is not None
            and existing.bank_details is not None
            and bank_details.strip() != existing.bank_details.strip()
        )

        result = {
            "status": existing.approval_status,
            "payable": existing.approval_status == "active",
            "bank_details_differ": conflicting,
            "detail": (
                f"{vendor_name!r} is already on file with status "
                f"{existing.approval_status!r}."
            ),
        }
        if conflicting:
            result["detail"] += (
                " This invoice states different bank details from the ones already on "
                f"file ({bank_details!r} against {existing.bank_details!r}). Neither has "
                "been verified. Treat the discrepancy as a finding in its own right."
            )
        return result

    session.add(
        Vendor(
            name=vendor_name.strip(),
            normalized_name=normalized,
            bank_details=bank_details,
            approval_status="pending_approval",
            created_by="agent",
        )
    )
    await session.commit()

    return {
        "status": "pending_approval",
        "payable": False,
        "detail": (
            f"{vendor_name!r} has been queued for human approval. Drafting a vendor "
            "does not authorise payment -- this invoice still requires review, and "
            "any bank details taken from the invoice are unverified."
        ),
    }


async def get_invoice_history(
    session: AsyncSession, vendor_id: str, lookback_days: int = 365
) -> dict:
    """Summarize what this vendor has already been paid.

    Aggregates, never rows: the question is "is this amount normal for them?".
    Range travels with the average because an average alone cannot tell a tight
    spend pattern from a wildly variable one.

    Only `approved` invoices feed the amount figures. Rejected ones are excluded
    for an adversarial reason rather than a tidy one: if refused amounts counted
    as normal, a payee could train the baseline by submitting $50,000 three
    times and having it refused. They are still reported as a bare count, which
    is a real signal about the payee but a different question.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # FILTER rather than WHERE: the two statuses aggregate over different
    # subsets of the same rows, and a WHERE could only express one of them.
    approved = Invoice.status == "approved"

    count, avg_amount, min_amount, max_amount, most_recent, rejected_count = (
        await session.execute(
            select(
                func.count(Invoice.id).filter(approved),
                func.avg(Invoice.amount).filter(approved),
                func.min(Invoice.amount).filter(approved),
                func.max(Invoice.amount).filter(approved),
                func.max(Invoice.created_at).filter(approved),
                func.count(Invoice.id).filter(Invoice.status == "rejected"),
            ).where(
                Invoice.vendor_id == vendor_id,
                Invoice.created_at >= since,
            )
        )
    ).one()

    def _as_float(value):
        # None stays None: "no history" and "bills nothing" are different
        # findings, and collapsing them makes every new vendor look anomalous.
        return float(value) if value is not None else None

    return {
        "count": count or 0,
        "lookback_days": lookback_days,
        "average_amount": _as_float(avg_amount),
        "min_amount": _as_float(min_amount),
        "max_amount": _as_float(max_amount),
        "most_recent_date": most_recent.isoformat() if most_recent else None,
        "rejected_count": rejected_count or 0,
    }


async def check_duplicate_invoice(
    session: AsyncSession,
    vendor_id: str,
    amount: float,
    invoice_number: str | None = None,
    exclude_invoice_id: uuid.UUID | None = None,
) -> dict:
    """Check whether this invoice has already been paid, or already refused.

    Distinguishes `exact` from `near` because the two demand different actions:
    an exact re-submission can be rejected, a near match belongs in front of a
    human. `previously_rejected` answers a third question -- a refused invoice
    is not a payment, but resubmitting one unchanged should not come back clean
    with the standing decision invisible.

    `exclude_invoice_id` keeps the invoice under review from matching itself,
    and is bound by the caller rather than supplied by the model.
    """
    resembles = [Invoice.amount == amount]
    if invoice_number:
        resembles.append(Invoice.invoice_number == invoice_number)

    def _candidates(status: str):
        stmt = select(Invoice).where(
            Invoice.vendor_id == vendor_id,
            Invoice.status == status,
            or_(*resembles),
        )
        if exclude_invoice_id is not None:
            stmt = stmt.where(Invoice.id != exclude_invoice_id)
        return stmt

    def _is_exact(prior: Invoice) -> bool:
        return (
            invoice_number is not None
            and prior.invoice_number == invoice_number
            and float(prior.amount) == amount
        )

    # Paid invoices first: money already out the door outranks a prior refusal.
    paid = (await session.execute(_candidates("approved"))).scalars().all()

    if paid:
        prior = next((c for c in paid if _is_exact(c)), None)
        match = "exact" if prior is not None else "near"
        if prior is None:
            prior = paid[0]

        return {
            "match": match,
            "detail": (
                "Identical invoice number and amount already paid."
                if match == "exact"
                else "A prior payment to this vendor closely resembles this invoice."
            ),
            "prior_invoice": {
                "invoice_number": prior.invoice_number,
                "amount": float(prior.amount) if prior.amount is not None else None,
                "paid_on": prior.created_at.isoformat() if prior.created_at else None,
            },
        }

    # Nothing paid, so ask the other question. Reported under its own match
    # rather than as a duplicate: the finding is the standing decision, not a
    # second payment.
    refused = (await session.execute(_candidates("rejected"))).scalars().all()

    if refused:
        prior = next((c for c in refused if _is_exact(c)), None)
        identical = prior is not None
        if prior is None:
            prior = refused[0]

        return {
            "match": "previously_rejected",
            "detail": (
                (
                    "An identical invoice from this vendor was already rejected by a "
                    "reviewer."
                    if identical
                    else "An invoice from this vendor closely resembling this one was "
                    "already rejected by a reviewer."
                )
                + " Treat that decision as standing unless something has changed;"
                " it was made by a person, and this tool cannot see why."
            ),
            "prior_invoice": {
                "invoice_number": prior.invoice_number,
                "amount": float(prior.amount) if prior.amount is not None else None,
                "rejected_on": prior.created_at.isoformat() if prior.created_at else None,
            },
        }

    return {
        "match": "none",
        "detail": "No prior payment or rejection from this vendor resembles it.",
    }


async def get_purchase_order(
    session: AsyncSession,
    po_number: str,
    invoice_amount: float | None = None,
    invoice_currency: str | None = None,
) -> dict:
    """Look up a purchase order and measure how far the invoice diverges from it.

    Reports arithmetic, not a verdict. The variance is computed here because
    percentage arithmetic is what a model gets subtly wrong; whether it is
    *acceptable* is left open, because that threshold lives in the AP policy.
    Encoding it here would also let the agent clear the PO cases without ever
    consulting the policy.
    """
    po = (
        await session.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
    ).scalar_one_or_none()

    if po is None:
        return {
            "exists": False,
            "po_number": po_number,
            "detail": f"No purchase order {po_number!r} exists in the system.",
        }

    po_amount = float(po.amount)
    result = {
        "exists": True,
        "po_number": po.po_number,
        "po_amount": po_amount,
        "po_currency": po.currency,
        "invoice_currency": invoice_currency,
    }

    # None, not False, when either side is unrecorded: "we know they differ" and
    # "we cannot tell" are different findings.
    currencies_known = po.currency is not None and invoice_currency is not None
    result["currency_match"] = (
        po.currency == invoice_currency if currencies_known else None
    )

    if currencies_known and po.currency != invoice_currency:
        # No variance: subtracting figures in different units produces a number
        # that looks authoritative and means nothing.
        result["detail"] = (
            f"Invoice is in {invoice_currency} and the purchase order in "
            f"{po.currency}; the amounts differ in unit and cannot be compared. "
            "No exchange rate is available to this tool."
        )
        return result

    if invoice_amount is not None:
        variance = invoice_amount - po_amount
        result["invoice_amount"] = invoice_amount
        result["variance_amount"] = round(variance, 2)
        result["variance_percent"] = (
            round(abs(variance) / po_amount * 100, 2) if po_amount else None
        )

    return result


def submit_recommendation(decision: str, confidence: float, reasoning: str) -> dict:
    """Record the agent's final decision, subject to a confidence floor.

    The agent proposes; this function decides. Below the threshold the decision
    becomes `escalate` regardless -- a model that is confidently wrong must not
    be able to talk its way past human review. The override applies to
    rejections too: wrongly refusing a legitimate invoice stalls a real payment,
    so uncertainty in either direction goes to a person.

    Malformed input escalates rather than raising, which would abort the run and
    leave the invoice with no decision at all.
    """
    reasons: list[str] = []

    if decision not in VALID_DECISIONS:
        reasons.append(f"decision {decision!r} is not one of {VALID_DECISIONS}")
    if not 0.0 <= confidence <= 1.0:
        reasons.append(f"confidence {confidence} is outside 0.0-1.0")
    elif confidence < settings.confidence_escalation_threshold:
        reasons.append(
            f"confidence {confidence} is below the "
            f"{settings.confidence_escalation_threshold} threshold for automated action"
        )

    # An explicit escalate is already what the floor would force, so it is not
    # an override -- flagging it would read as overruling an agent that agreed.
    overridden = bool(reasons) and decision != "escalate"
    final_decision = "escalate" if reasons else decision

    return {
        "original_decision": decision,
        "final_decision": final_decision,
        "overridden": overridden,
        "override_reason": "; ".join(reasons) if overridden else None,
        "confidence": confidence,
        "reasoning": reasoning,
    }


async def search_policy_tool(session: AsyncSession, query: str) -> dict:
    """Search the written AP policy for the clauses governing this invoice.

    Named `_tool` because `app.rag.search.search_policy` is the retrieval
    function it wraps; the agent-facing name stays `search_policy`.

    Each clause carries its section, which is what lets the agent cite "per §II"
    and lets the groundedness grader check that citation. Similarity scores are
    withheld for the same reason `lookup_vendor` withholds them.
    """
    documents = await rag_search_policy(session, query=query, top_k=POLICY_TOP_K)

    if not documents:
        # pgvector always returns the nearest rows, so nothing coming back means
        # the table is empty -- the corpus was never loaded.
        return {
            "clauses": [],
            "detail": (
                "The policy corpus is empty -- nothing has been indexed to search. "
                "Treat this as a missing tool, not as the policy being silent."
            ),
        }

    return {
        "clauses": [{"section": d.section, "text": d.chunk_text} for d in documents],
    }
