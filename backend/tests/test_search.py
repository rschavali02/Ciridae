import pytest

from app.rag.chunking import PolicyChunk
from app.rag.search import search_policy
from app.rag.store import store_policy_chunks

CORPUS = [
    PolicyChunk(
        section="II. Policy",
        text=(
            "For purchase order based payments, discrepancies between the vendor invoice "
            "and the purchase order greater than 10 percent or $1,000 USD or equivalent in "
            "local currency (the lesser of the two) must be resolved before the payment can "
            "be processed."
        ),
    ),
    PolicyChunk(
        section="A. Segregation of duties",
        text=(
            "There must be an appropriate segregation of functional responsibilities so that "
            "no individual controls all phases of a transaction."
        ),
    ),
    PolicyChunk(
        section="F. Currency of payments",
        text=(
            "With the exception of headquarters locations, goods and services should be paid "
            "for in the local currency of the business unit."
        ),
    ),
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_finds_the_governing_clause_without_sharing_its_words(db_session):
    """The query says "differ from its PO"; the clause says "discrepancies" and
    "greater than 10 percent". No keyword overlap worth the name -- if this
    passes on wording alone the embedding is not doing anything."""
    await store_policy_chunks(db_session, CORPUS)

    results = await search_policy(db_session, query="how much can an invoice differ from its PO")

    assert results[0].section == "II. Policy"
    assert "10 percent" in results[0].chunk_text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_routes_a_different_question_to_a_different_clause(db_session):
    """Guards against the degenerate case where one chunk wins every query.

    Asserts retrieval, not rank. "Can one person both request and approve a
    payment" puts the segregation clause second, behind II. Policy, because the
    query says "payment" and II. Policy is thick with the word while segregation
    talks about an "individual" controlling "phases of a transaction". That is
    the ordinary way retrieval misses -- vocabulary, not meaning -- and it is
    why the tool hands the agent five clauses rather than one. Pinning rank 1
    would assert a guarantee this system does not make and does not need.
    """
    await store_policy_chunks(db_session, CORPUS)

    currency = await search_policy(db_session, query="what if the invoice is billed in euros")
    duties = await search_policy(db_session, query="can one person both request and approve a payment")

    assert "F. Currency of payments" in [r.section for r in currency]
    assert "A. Segregation of duties" in [r.section for r in duties]
    assert currency[0].section != duties[0].section, "one chunk is winning every query"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_top_k_bounds_the_result_count(db_session):
    await store_policy_chunks(db_session, CORPUS)

    assert len(await search_policy(db_session, query="payment rules", top_k=2)) == 2
    assert len(await search_policy(db_session, query="payment rules", top_k=99)) == len(CORPUS)
