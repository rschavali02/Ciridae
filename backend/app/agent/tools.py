"""Plain implementations of the agent's tools.

These are ordinary async functions taking a DB session, which keeps them
unit-testable in isolation. `app/agent/runner.py` wraps each one in a decorated
closure that binds the session and transcript, so the schema Claude sees never
mentions either.

Each returns a small, high-signal dict rather than raw rows -- the agent should
be handed a conclusion it can reason about, not a table it has to summarize.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
