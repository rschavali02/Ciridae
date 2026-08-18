"""Split the AP policy into citable chunks.

Splitting on the document's own numbered headings -- roman numerals, letters,
and `Step N:` markers -- making a retrieved clause citable.
"""

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(?:(?:[IVX]+|[A-H])\.\s+\S|Step\s+\d+\s*:)")

# Repeats on every page. Embedded per page, it outweighs the rules around it.
PAGE_FURNITURE = {
    "UNFPA",
    "Policies and Procedures Manual",
    "Policy and Procedures on Accounts Payable",
}

# Past this a chunk is a chapter rather than a rule.
MAX_CHUNK_CHARS = 1500


@dataclass
class PolicyChunk:
    section: str  
    text: str  

    @property
    def embed_text(self) -> str:
        """Heading plus body, so a query can match on the section a rule belongs to."""
        return f"{self.section}\n{self.text}"


def _is_noise(line: str) -> bool:
    if not line:
        return True
    if line in PAGE_FURNITURE:
        return True
    if line.isdigit():  
        return True
    if line.startswith("Effective date:"):
        return True
    if "....." in line: 
        return True
    return False


def _split_long(section: str, body: str) -> list[PolicyChunk]:
    """Sub-split an over-long section on sentence boundaries, never mid-clause."""
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
