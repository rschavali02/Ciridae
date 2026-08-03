"""Seed the vendors table with the vendors used by the invoice fixtures.

Run: python -m fixtures.seed_vendors

`normalized_name` is what `lookup_vendor` fuzzy-matches against via pg_trgm,
so it's stored lowercased. The invoice PDFs deliberately print vendor names
differently from these records (e.g. "Acme Inc" vs "ACME Incorporated") so
name drift is something the agent has to resolve at decision time.
"""
import asyncio

from app.db import SessionLocal
from app.models import Vendor

VENDORS = [
    {
        "name": "ACME Incorporated",
        "normalized_name": "acme incorporated",
        "bank_details": "IBAN GB00ACME00000000000001",
    },
    {
        "name": "Globex Corp",
        "normalized_name": "globex corp",
        "bank_details": "IBAN GB00GLBX00000000000002",
    },
]


async def seed() -> None:
    async with SessionLocal() as session:
        for vendor in VENDORS:
            session.add(Vendor(**vendor))
        await session.commit()
    print(f"seeded {len(VENDORS)} vendors")


if __name__ == "__main__":
    asyncio.run(seed())
