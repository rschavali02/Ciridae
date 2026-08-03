"""Plain implementations of the agent's tools.

These are ordinary async functions taking a DB session, which keeps them
unit-testable in isolation. `app/agent/runner.py` wraps each one in a decorated
closure that binds the session and transcript, so the schema Claude sees never
mentions either.

Each returns a small, high-signal dict rather than raw rows -- the agent should
be handed a conclusion it can reason about, not a table it has to summarize.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Invoice

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

    Returns one of three shapes, because the agent needs to act differently on
    each: a clean resolution, nothing close enough, or several equally close
    candidates (policy IV requires escalating the last two).
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT id, name, bank_details,
                       similarity(normalized_name, :name) AS sim
                FROM vendors
                WHERE similarity(normalized_name, :name) > :threshold
                ORDER BY sim DESC
                LIMIT 2
                """
            ),
            {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
        )
    ).all()

    if not rows:
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
        "similarity": round(best.sim, 2),
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
