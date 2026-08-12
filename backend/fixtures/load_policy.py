"""Load the AP policy into the `documents` table.

Run: python -m fixtures.load_policy

The eval harness deliberately does not wipe `documents` between trials, so the
corpus must be sitting in the database before a run rather than being built
inside it -- an empty table makes `search_policy` return nothing and the whole
Phase 5 comparison measures a broken tool instead of retrieval.

Replaces rather than appends. Running this twice against an appending loader
would leave two copies of all 25 chunks, and duplicates crowd a top-5 result
with the same clause twice, pushing out the clause the agent actually needed.
"""

import asyncio

import pdfplumber
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Document
from app.rag.chunking import chunk_policy_text
from app.rag.store import store_policy_chunks

POLICY_PDF = "fixtures/policy/FINA_Accounts_Payable.pdf"


def extract_policy_text(path: str = POLICY_PDF) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


async def load() -> None:
    text = extract_policy_text()
    chunks = chunk_policy_text(text)
    print(f"chunked {len(text):,} characters into {len(chunks)} chunks")

    async with SessionLocal() as session:
        existing = len((await session.execute(select(Document))).scalars().all())
        if existing:
            print(f"replacing {existing} existing chunks")
            await session.execute(delete(Document))
            await session.commit()

        await store_policy_chunks(session, chunks)

    print(f"stored {len(chunks)} chunks")


if __name__ == "__main__":
    asyncio.run(load())
