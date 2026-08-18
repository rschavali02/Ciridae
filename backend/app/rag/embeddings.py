"""Embeddings for the policy corpus.

Anthropic has no embeddings endpoint, so this is the one part of the pipeline
that talks to a different provider.
"""

import voyageai

from app.config import settings

client = voyageai.Client(api_key=settings.voyage_api_key, max_retries=8, timeout=30)

#matches document.embedding width
EMBED_DIM = 1024

MODEL = "voyage-3.5-lite"


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts.

    Pass "document" when storing a chunk and "query" when searching: Voyage
    tunes the two differently and the wrong one measurably degrades retrieval.
    """
    return client.embed(texts, model=MODEL, input_type=input_type).embeddings
