import pytest
from sqlalchemy import select

from app.models import Document
from app.rag.chunking import PolicyChunk
from app.rag.embeddings import EMBED_DIM
from app.rag.store import store_policy_chunks

CHUNKS = [
    PolicyChunk(
        section="II. Policy",
        text="Discrepancies greater than 10 percent or $1,000 USD must be resolved.",
    ),
    PolicyChunk(
        section="A. Segregation of duties",
        text="There must be an appropriate segregation of functional responsibilities.",
    ),
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stores_one_row_per_chunk_with_its_heading(db_session):
    await store_policy_chunks(db_session, CHUNKS)

    rows = (await db_session.execute(select(Document))).scalars().all()
    assert len(rows) == 2
    assert {r.section for r in rows} == {"II. Policy", "A. Segregation of duties"}
    assert all(len(r.embedding) == EMBED_DIM for r in rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stores_the_body_alone_not_the_embedded_heading(db_session):
    """The heading is embedded so it influences retrieval, but stored separately
    so the agent is shown the rule and cites the section, rather than reading a
    heading spliced into the middle of the text it quotes."""
    await store_policy_chunks(db_session, CHUNKS)

    row = (
        await db_session.execute(select(Document).where(Document.section == "II. Policy"))
    ).scalar_one()
    assert row.chunk_text == CHUNKS[0].text
    assert "II. Policy" not in row.chunk_text
