"""Similarity search over the policy corpus."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.rag.embeddings import embed_texts

# Enough clauses that a rule split across two sections still arrives whole --
# the §II tolerance appears both under "II. Policy" and under "Step 1" -- while
# staying short enough that the agent is not handed a wall of policy to skim.
DEFAULT_TOP_K = 5


async def search_policy(
    session: AsyncSession, query: str, top_k: int = DEFAULT_TOP_K
) -> list[Document]:
    """Return the policy chunks closest to `query`, nearest first.

    No filtering clause is needed: `documents` holds policy chunks and nothing
    else. Invoice text is deliberately never embedded -- the agent already has
    the invoice in its opening prompt, and retrieving text from *other* invoices
    would put foreign amounts in front of an agent judging this one.
    """
    # input_type="query" matters here. Voyage tunes a question differently from
    # a passage, and embedding a query as though it were a document measurably
    # degrades what comes back.
    query_embedding = embed_texts([query], input_type="query")[0]

    stmt = (
        select(Document)
        .order_by(Document.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    return (await session.execute(stmt)).scalars().all()
