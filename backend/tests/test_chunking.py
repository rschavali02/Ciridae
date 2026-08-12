"""The policy PDF resists the obvious chunking strategy, and these pin down why.

The sample below is a faithful miniature of the real extraction: no blank lines
anywhere, a table of contents whose entries look exactly like headings, and page
furniture repeating between sections.
"""

from app.rag.chunking import chunk_policy_text

SAMPLE = """UNFPA
Policies and Procedures Manual
Policy and Procedures on Accounts Payable
I. Purpose .................................................................... 1
II. Policy ..................................................................... 2
UNFPA
Policies and Procedures Manual
Policy and Procedures on Accounts Payable
I. Purpose
This policy establishes the procedures for the payment of purchase
order and non-purchase order procured goods and services.
1
Effective date: September 2016
II. Policy
For purchase order based payments, discrepancies between the vendor
invoice and the purchase order greater than 10 percent or $1,000 USD
must be resolved before the payment can be processed.
2
Effective date: September 2016
"""


def test_splits_on_section_headings():
    sections = [c.section for c in chunk_policy_text(SAMPLE)]
    assert "I. Purpose" in sections
    assert "II. Policy" in sections


def test_drops_table_of_contents_entries():
    """TOC lines look like headings but trail dot leaders. Left in, every section
    gains a duplicate empty chunk and every search returns two of everything."""
    chunks = chunk_policy_text(SAMPLE)
    assert not any("....." in c.text for c in chunks)
    assert sum(1 for c in chunks if c.section == "I. Purpose") == 1


def test_strips_repeating_page_furniture():
    """The header and footer repeat on all 15 pages of the real document. Left
    in, they are embedded 15 times each and pollute every similarity search."""
    body = " ".join(c.text for c in chunk_policy_text(SAMPLE))
    assert "Policies and Procedures Manual" not in body
    assert "Effective date: September 2016" not in body


def test_rejoins_wrapped_lines_into_flowing_text():
    """Extraction breaks every line, so the governing clause arrives in three
    pieces. A chunk split mid-sentence retrieves badly and cites worse."""
    policy = next(c for c in chunk_policy_text(SAMPLE) if c.section == "II. Policy")
    assert "greater than 10 percent or $1,000 USD must be resolved" in policy.text


def test_embed_text_carries_the_heading():
    """What gets embedded is heading plus body, so a query about purchase order
    tolerances can match on the section a rule lives under, not only its wording."""
    policy = next(c for c in chunk_policy_text(SAMPLE) if c.section == "II. Policy")
    assert policy.embed_text.startswith("II. Policy")
    assert "10 percent" in policy.embed_text
