"""Similarity search over the policy corpus."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.rag.embeddings import embed_texts

DEFAULT_TOP_K = 5


async def search_policy(
    session: AsyncSession, query: str, top_k: int = DEFAULT_TOP_K
) -> list[Document]:
    """Return the policy chunks closest to `query`, nearest first.

    No filtering clause is needed: `documents` holds policy chunks and nothing
    else.
    """
    # input_type="query", not "document" -- see embed_texts.
    query_embedding = embed_texts([query], input_type="query")[0]

    stmt = (
        select(Document)
        .order_by(Document.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    return (await session.execute(stmt)).scalars().all()
