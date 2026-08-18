"""Persist policy chunks with their embeddings."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document
from app.rag.chunking import PolicyChunk
from app.rag.embeddings import embed_texts


async def store_policy_chunks(
    session: AsyncSession, chunks: list[PolicyChunk]
) -> list[Document]:
    """Embed each chunk and store it.

    What is embedded and what is stored differ: the vector is built from
    heading plus body so a query can match on the section a rule belongs to,
    while `chunk_text` holds the body alone, since the heading is shown
    separately as the citation.
    """
    embeddings = embed_texts([chunk.embed_text for chunk in chunks], input_type="document")

    documents = [
        Document(section=chunk.section, chunk_text=chunk.text, embedding=embedding)
        for chunk, embedding in zip(chunks, embeddings)
    ]
    session.add_all(documents)
    await session.commit()
    return documents
