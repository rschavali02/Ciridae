"""Plain implementations of the agent's tools.

These are ordinary async functions taking a DB session, which keeps them
unit-testable in isolation. `app/agent/runner.py` wraps each one in a decorated
closure that binds the session and transcript, so the schema Claude sees never
mentions either.

Each returns a small, high-signal dict rather than raw rows -- the agent should
be handed a conclusion it can reason about, not a table it has to summarize.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Invoice, PurchaseOrder
from app.rag.search import DEFAULT_TOP_K as POLICY_TOP_K
from app.rag.search import search_policy as rag_search_policy

VALID_DECISIONS = ("approve", "reject", "escalate")

# Set from measured trigram scores against the seeded vendors, not by feel:
#
#   legitimate variants   "acme" 0.278 .. "acme incorporated llc" 0.818
#   unrelated names       0.000 .. 0.071  ("globex corp" vs "acme incorporated")
#
# 0.2 sits in the empty gap between those bands, with room on both sides. The
# plan's original 0.4 fell *inside* the legitimate band and would have rejected
# "acme" -- escalating an invoice that should resolve cleanly.
#
# This is tuned against two seeded vendors. A real vendor master with hundreds
# of names has a much fatter false-positive tail, so re-measure before trusting
# this threshold on production data.
SIMILARITY_THRESHOLD = 0.2

# If the runner-up is within this of the best match, the name does not identify
# a single vendor and the agent must be told so rather than handed a winner.
AMBIGUITY_MARGIN = 0.10


async def lookup_vendor(session: AsyncSession, vendor_name: str) -> dict:
    """Resolve a printed vendor name against the vendor master file.

    Returns one of four shapes, because the agent needs to act differently on
    each: a clean resolution, nothing close enough, several equally close
    candidates (policy IV requires escalating the last two), or a payee already
    drafted and waiting on a human.

    Only `active` vendors resolve. A drafted vendor must never match here, or
    the next invoice from that payee resolves cleanly and the approval control
    disappears rather than holding.
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
        # A second query rather than one filtered in Python, so the agent is
        # told the difference between "nobody by that name" and "already
        # drafted, waiting on a human" -- the two call for different actions.
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
            return {
                "match": "drafted",
                "detail": (
                    f"{vendor_name!r} has already been drafted as a new vendor and is "
                    "awaiting approval. It is not yet payable."
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

    return {
        "match": "resolved",
        "vendor_id": str(best.id),
        "vendor_name": best.name,
        "bank_details": best.bank_details,
        # The raw similarity score is deliberately withheld on a resolved match.
        #
        # It is calibrated against a measured distribution the agent has no
        # access to: 0.42 is an unremarkable score for "Acme Inc" vs "ACME
        # Incorporated", but on a 0-1 scale it reads as poor, and in the first
        # live run the agent cited exactly that figure as its reason for cutting
        # confidence to 0.82. A slightly shorter abbreviation would score lower
        # still and could push a clean invoice under the escalation floor.
        #
        # Deciding whether the score clears the bar is this tool's job, already
        # done. Passing the number along invites the agent to re-litigate a
        # calibrated judgment with an uncalibrated one. Scores are still shown
        # on the ambiguous branch, where they are used comparatively -- how
        # close two candidates are -- rather than as an absolute quality signal.
    }


async def get_invoice_history(
    session: AsyncSession, vendor_id: str, lookback_days: int = 365
) -> dict:
    """Summarize what this vendor has already been paid.

    Returns aggregates, never rows. The agent's question is "is this amount
    normal for them?", and handing it a summary answers that directly, whereas
    handing it 200 invoices makes it do arithmetic it is bad at and burns
    context on data nobody reads again.

    Range is included alongside the average deliberately: an average alone
    cannot tell a tight spend pattern from a wildly variable one, so on its own
    it is a weak basis for calling an amount anomalous.

    Only `approved` invoices count. History means what we have actually paid --
    counting the pending invoice under review would fold it into its own
    baseline and shrink the very anomaly the agent is looking for.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    count, avg_amount, min_amount, max_amount, most_recent = (
        await session.execute(
            select(
                func.count(Invoice.id),
                func.avg(Invoice.amount),
                func.min(Invoice.amount),
                func.max(Invoice.amount),
                func.max(Invoice.created_at),
            ).where(
                Invoice.vendor_id == vendor_id,
                Invoice.status == "approved",
                Invoice.created_at >= since,
            )
        )
    ).one()

    def _as_float(value):
        # None stays None rather than becoming 0.0: "no history" and "bills
        # nothing" are different findings, and collapsing them would make every
        # invoice from a new vendor look anomalous.
        return float(value) if value is not None else None

    return {
        "count": count or 0,
        "lookback_days": lookback_days,
        "average_amount": _as_float(avg_amount),
        "min_amount": _as_float(min_amount),
        "max_amount": _as_float(max_amount),
        "most_recent_date": most_recent.isoformat() if most_recent else None,
    }


async def check_duplicate_invoice(
    session: AsyncSession,
    vendor_id: str,
    amount: float,
    invoice_number: str | None = None,
    exclude_invoice_id: uuid.UUID | None = None,
) -> dict:
    """Check whether this vendor has already been paid for this invoice.

    Distinguishes `exact` from `near`, because the two demand different
    actions: an exact re-submission is a confirmed duplicate and can be
    rejected, while a near match is only suspicious and belongs in front of a
    human. A single boolean would collapse that distinction and force the agent
    to guess which it was looking at.

    `exclude_invoice_id` keeps the invoice under review from matching itself.
    It is bound by the caller rather than supplied by the model -- the agent has
    no business naming which row to ignore, and forgetting it means every clean
    invoice reports as a duplicate of itself.
    """
    resembles = [Invoice.amount == amount]
    if invoice_number:
        resembles.append(Invoice.invoice_number == invoice_number)

    stmt = select(Invoice).where(
        Invoice.vendor_id == vendor_id,
        # "Already paid", not "already seen" -- a rejected invoice was never a
        # payment, so it cannot be the thing this one duplicates.
        Invoice.status == "approved",
        or_(*resembles),
    )
    if exclude_invoice_id is not None:
        stmt = stmt.where(Invoice.id != exclude_invoice_id)

    candidates = (await session.execute(stmt)).scalars().all()

    if not candidates:
        return {"match": "none", "detail": "No prior payment to this vendor resembles it."}

    def _is_exact(prior: Invoice) -> bool:
        return (
            invoice_number is not None
            and prior.invoice_number == invoice_number
            and float(prior.amount) == amount
        )

    prior = next((c for c in candidates if _is_exact(c)), None)
    match = "exact" if prior is not None else "near"
    if prior is None:
        prior = candidates[0]

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


async def get_purchase_order(
    session: AsyncSession,
    po_number: str,
    invoice_amount: float | None = None,
    invoice_currency: str | None = None,
) -> dict:
    """Look up a purchase order and measure how far the invoice diverges from it.

    Reports arithmetic, not a verdict. The variance is computed here because
    percentage arithmetic is exactly the sort of thing a model gets subtly
    wrong; whether that variance is *acceptable* is deliberately left open,
    because the governing threshold lives in the AP policy rather than in this
    codebase.

    That split is load-bearing in two ways. Encoding the threshold here would
    let the agent clear the PO-tolerance eval cases without ever consulting the
    policy, which would make the before/after RAG measurement meaningless. It
    would also put a business rule somewhere a policy revision cannot reach --
    the threshold would silently drift out of date the next time Finance
    reissues the document.
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
    # "we cannot tell" are different findings, and collapsing the second into
    # the first would hold every invoice predating the currency column.
    currencies_known = po.currency is not None and invoice_currency is not None
    result["currency_match"] = (
        po.currency == invoice_currency if currencies_known else None
    )

    if currencies_known and po.currency != invoice_currency:
        # Deliberately no variance. Subtracting figures in different units
        # produces a number that looks authoritative and means nothing.
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

    The agent proposes; this function decides. Below the configured threshold
    the decision becomes `escalate` regardless of what the agent asked for --
    the whole point of the floor is that a model which is confidently wrong
    must not be able to talk its way past human review.

    The override applies to rejections as well as approvals. Refusal is not the
    safe default: wrongly rejecting a legitimate invoice stalls a real payment
    and damages a vendor relationship. Both directions carry consequences, so
    uncertainty in either sends the invoice to a person.

    Malformed input escalates rather than raising. Raising would abort the run
    and leave the invoice with no decision at all, whereas escalating keeps it
    moving toward the person who can sort it out -- the same failure direction
    the confidence floor already encodes.
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

    # An explicit escalate is already the outcome the floor would force, so it
    # is not an override. Flagging it as one would make the audit trail read as
    # though the system overruled an agent that in fact agreed.
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
    function it wraps; the agent-facing name stays `search_policy`, bound in
    `build_tools`.

    Each clause carries the section it came from, not just its text. That is
    what lets the agent cite "per §II" rather than paraphrasing an unattributed
    rule, and what lets the groundedness grader check the citation against what
    was actually returned. A bare wall of text would make both impossible.

    Similarity scores are deliberately not returned, for the same reason
    `lookup_vendor` withholds them: they are calibrated against a distribution
    the agent cannot see, and inviting it to weigh a rule by its distance score
    replaces a retrieved fact with an uncalibrated judgement.
    """
    documents = await rag_search_policy(session, query=query, top_k=POLICY_TOP_K)

    if not documents:
        # pgvector always returns the nearest rows, so nothing coming back means
        # the table is empty -- the corpus was never loaded. Saying so keeps a
        # misconfigured eval run from looking like a policy that simply had no
        # answer, which would be scored as the agent's failure rather than ours.
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
