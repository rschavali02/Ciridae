"""Embeddings for the policy corpus.

Anthropic has no embeddings endpoint, so this is the one part of the pipeline
that talks to a different provider.
"""

import voyageai

from app.config import settings

# max_retries defaults to 0, which turns the free tier's requests-per-minute cap
# into a hard failure on the first throttle. That matters well beyond the tests:
# during an eval run the agent calls search_policy dozens of times inside twenty
# minutes, and an unretried 429 there aborts a run that costs real money.
client = voyageai.Client(api_key=settings.voyage_api_key, max_retries=8, timeout=30)

# Tied to the width of documents.embedding. A model whose output does not match
# is rejected by pgvector at insert time, with an error that points at the row
# rather than at the model choice that caused it -- `voyage-3-lite` returns 512
# and fails exactly that way, which is why the plan's original pairing of that
# model with a 1024-wide column could never have worked.
EMBED_DIM = 1024

MODEL = "voyage-3.5-lite"


def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    """Embed a batch of texts.

    `input_type` is load-bearing: Voyage embeds a passage being stored and a
    question being asked differently, and using the wrong one measurably
    degrades retrieval. Pass "document" when storing a chunk and "query" when
    searching -- the two remain directly comparable, they are simply tuned for
    their respective roles.
    """
    return client.embed(texts, model=MODEL, input_type=input_type).embeddings
