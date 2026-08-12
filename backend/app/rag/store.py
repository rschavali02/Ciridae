"""Persist policy chunks with their embeddings."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.rag.chunking import PolicyChunk
from app.rag.embeddings import embed_texts


async def store_policy_chunks(
    session: AsyncSession, chunks: list[PolicyChunk]
) -> list[Document]:
    """Embed each chunk and store it.

    What gets embedded and what gets stored deliberately differ. The vector is
    built from `embed_text` -- heading plus body -- so a query about purchase
    order tolerances can match on the part of the policy a rule belongs to, not
    only on its wording. `chunk_text` holds the body alone, because that is what
    is shown back to the agent, with the heading supplied separately as the
    citation.

    One batched call rather than one per chunk: the corpus is 25 chunks and the
    free tier's rate limit is per request, not per token.
    """
    embeddings = embed_texts([chunk.embed_text for chunk in chunks], input_type="document")

    documents = [
        Document(section=chunk.section, chunk_text=chunk.text, embedding=embedding)
        for chunk, embedding in zip(chunks, embeddings)
    ]
    session.add_all(documents)
    await session.commit()
    return documents
