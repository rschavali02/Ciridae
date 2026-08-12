import pytest

from app.models import Document
from app.rag.embeddings import EMBED_DIM, embed_texts


def test_dimension_matches_the_column_it_is_stored_in():
    """The one failure this catches is otherwise reported at insert time as an
    opaque pgvector error, a long way from the model choice that caused it.
    `voyage-3-lite` returns 512 and would fail exactly that way."""
    column_width = Document.__table__.c.embedding.type.dim
    assert EMBED_DIM == column_width


@pytest.mark.integration
def test_embed_texts_returns_one_vector_per_input():
    vectors = embed_texts(["hello world", "invoice total due"])
    assert len(vectors) == 2
    assert all(len(v) == EMBED_DIM for v in vectors)


@pytest.mark.integration
def test_query_and_document_embeddings_land_in_the_same_space():
    """Voyage embeds queries and documents asymmetrically, so the two calls are
    not interchangeable -- but they must remain comparable, or cosine distance
    between a stored chunk and a search query is meaningless."""
    document = embed_texts(["Discrepancies greater than 10 percent must be resolved."])[0]
    query = embed_texts(["how much can an invoice differ from its PO"], input_type="query")[0]

    assert len(document) == len(query) == EMBED_DIM
    similarity = sum(a * b for a, b in zip(document, query))
    assert similarity > 0.3, f"unexpectedly unrelated: {similarity:.3f}"
