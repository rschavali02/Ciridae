"""Load the AP policy into the `documents` table, replacing any existing chunks.

Run: python -m fixtures.load_policy

Nothing else populates the corpus, and `search_policy` returns nothing until it is.
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
