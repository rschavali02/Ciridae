"""Reset the database to a clean, demo-ready state and seed the Stark
Industries scenario.

Run: python -m fixtures.seed_demo

Wipes every data-bearing table except `documents` (the policy corpus --
re-embedding it costs real Voyage calls for no reason before a demo) and
reseeds:

- The standing vendor master: ACME Incorporated, Globex Corp, Stark
  Industries -- all `active`, so lookup_vendor resolves cleanly.
- Three past *approved* invoices for Stark Industries, bracketing the demo
  invoice's $9,780.10. Without these, get_invoice_history returns count=0 for
  a first-time payee, and the agent has been observed treating that as a
  caution worth a lower confidence -- a real risk in an unscripted live run
  with no second take. None of the seeded amounts equal $9,780.10 or each
  other's invoice numbers: check_duplicate_invoice matches on amount OR
  invoice number, so a collision would misfire as a duplicate finding.

The Stark Industries invoice PDF itself (fixtures/invoices/invoice_05.pdf) is
uploaded live during the demo, not seeded here -- seeding the row would mean
demoing an invoice that already has a decision.
"""

import asyncio

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Invoice, Vendor

# Same order used by the eval harness's per-trial reset: children before the
# tables they reference. `documents` (the policy corpus) is deliberately
# excluded -- it costs real embedding calls to rebuild and nothing here
# touches it.
_TABLES = ("agent_runs", "audit_log", "line_items", "invoices", "purchase_orders", "vendors")

VENDORS = [
    {
        "name": "ACME Incorporated",
        "normalized_name": "acme incorporated",
        "bank_details": "IBAN GB00ACME00000000000001",
        "approval_status": "active",
    },
    {
        "name": "Globex Corp",
        "normalized_name": "globex corp",
        "bank_details": "IBAN GB00GLBX00000000000002",
        "approval_status": "active",
    },
    {
        "name": "Stark Industries",
        "normalized_name": "stark industries",
        "bank_details": "IBAN GB00STRK00000000000003",
        "approval_status": "active",
    },
]

# Brackets the $9,780.10 demo invoice (INV-1004) without matching it or each
# other -- see the module docstring for why an exact match would misfire.
STARK_HISTORY = [
    {"invoice_number": "INV-0901", "amount": 9200.00},
    {"invoice_number": "INV-0930", "amount": 9650.00},
    {"invoice_number": "INV-0965", "amount": 10100.00},
]


async def seed() -> None:
    async with SessionLocal() as session:
        for table in _TABLES:
            await session.execute(text(f"DELETE FROM {table}"))

        vendors = [Vendor(**v) for v in VENDORS]
        session.add_all(vendors)
        await session.flush()

        stark = next(v for v in vendors if v.name == "Stark Industries")
        for past in STARK_HISTORY:
            session.add(
                Invoice(
                    vendor_id=stark.id,
                    currency="USD",
                    status="approved",
                    raw_pdf_path="fixtures/invoices/historical.pdf",
                    **past,
                )
            )

        await session.commit()

    print(f"reset {len(_TABLES)} tables")
    print(f"seeded {len(VENDORS)} vendors: {', '.join(v['name'] for v in VENDORS)}")
    print(f"seeded {len(STARK_HISTORY)} approved past invoices for Stark Industries")


if __name__ == "__main__":
    asyncio.run(seed())
