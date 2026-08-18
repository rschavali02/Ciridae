"""Seed the vendor master with the vendors the invoice fixtures bill from.

Run: python -m fixtures.seed_vendors

The PDFs print these names differently ("Acme Inc" against "ACME Incorporated"),
so resolving the drift is the agent's job.
"""
import asyncio

from app.db import SessionLocal
from app.models import Vendor

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
]


async def seed() -> None:
    async with SessionLocal() as session:
        for vendor in VENDORS:
            session.add(Vendor(**vendor))
        await session.commit()
    print(f"seeded {len(VENDORS)} vendors")


if __name__ == "__main__":
    asyncio.run(seed())
