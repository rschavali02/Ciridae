"""Split the AP policy into citable chunks.

The obvious approach does not work here. Splitting on `\\n\\n` returns the entire
26,000-character document as a single chunk, because PDF extraction produces no
blank lines at all -- every line break is a wrap, not a paragraph. That failure
is silent: you get one chunk back and a retrieval tool that always returns the
whole policy.

So the split runs on the document's own numbered headings, which survive
extraction intact: roman numerals (`I. Purpose`), letters (`A. Segregation of
duties`), and `Step N:` markers. Each chunk keeps its heading, which is what
makes a retrieved clause citable -- the agent can say "per §II" and the
groundedness grader can check that against the text it was given.

Three properties of the real document force the cleanup below: page furniture
repeats on all 15 pages, the table of contents mimics headings, and some
sections run past a comfortable chunk size.
"""

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(?:(?:[IVX]+|[A-H])\.\s+\S|Step\s+\d+\s*:)")

# Repeats on every page of the source PDF. Embedded once per page, this
# boilerplate outweighs the rules it surrounds and surfaces on every query.
PAGE_FURNITURE = {
    "UNFPA",
    "Policies and Procedures Manual",
    "Policy and Procedures on Accounts Payable",
}

# Past this, a chunk is a chapter rather than a rule: retrieval returns a wall of
# text, most of it irrelevant to the query that matched one sentence inside it.
MAX_CHUNK_CHARS = 1500


@dataclass
class PolicyChunk:
    section: str  # the heading this chunk lives under, e.g. "II. Policy"
    text: str  # the rule itself, with wrapped lines rejoined

    @property
    def embed_text(self) -> str:
        """Heading plus body, which is what gets embedded.

        A query like "how much can an invoice differ from its PO" should be able
        to match on the part of the policy a rule belongs to, not only on the
        wording of the rule. The body alone is stored for display, since the
        heading is shown separately.
        """
        return f"{self.section}\n{self.text}"


def _is_noise(line: str) -> bool:
    if not line:
        return True
    if line in PAGE_FURNITURE:
        return True
    if line.isdigit():  # bare page number
        return True
    if line.startswith("Effective date:"):
        return True
    if "....." in line:  # table-of-contents entry, trailing dot leaders
        return True
    return False


def _split_long(section: str, body: str) -> list[PolicyChunk]:
    """Sub-split an over-long section on sentence boundaries.

    Sentence boundaries rather than a character count, so a chunk never ends
    mid-clause -- a rule cut in half retrieves poorly and cites misleadingly.
    """
    if len(body) <= MAX_CHUNK_CHARS:
        return [PolicyChunk(section=section, text=body)]

    sentences = re.split(r"(?<=[.:])\s+", body)
    chunks: list[PolicyChunk] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS:
            chunks.append(PolicyChunk(section=section, text=current.strip()))
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(PolicyChunk(section=section, text=current.strip()))
    return chunks


def chunk_policy_text(text: str) -> list[PolicyChunk]:
    """Split extracted policy text into chunks, one heading each."""
    sections: list[tuple[str, list[str]]] = []

    for raw in text.split("\n"):
        line = raw.strip()
        if _is_noise(line):
            continue
        if HEADING_RE.match(line):
            sections.append((line, []))
            continue
        if sections:
            sections[-1][1].append(line)
        # Lines before the first heading are cover-page front matter -- dropped.

    chunks: list[PolicyChunk] = []
    for section, lines in sections:
        body = " ".join(lines).strip()
        if not body:
            continue  # a heading whose only occurrence was in the contents list
        chunks.extend(_split_long(section, body))
    return chunks
