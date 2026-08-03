# Invoice Agent Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the backend for the invoice agent — extraction pipeline, RAG, a tool-using agent, and an eval harness — so it can be driven from a script/API before any frontend exists.

**Architecture:** Work outward from the messy PDFs themselves: get extraction working as plain Python first (no database at all), then add persistence only once the agent's tools need to query it. The agent uses the Anthropic SDK's **Tool Runner** (`client.beta.messages.tool_runner`), which drives the request → execute → loop cycle over tools we define. Policy RAG is added **last, as a measured second pass** — see Build order below.

**Tech Stack:** Python 3.12, `pdfplumber`, `anthropic` SDK, `voyageai` SDK, Postgres 16 + pgvector (Docker Compose), SQLAlchemy 2.0 (async, `asyncpg` driver), Alembic, FastAPI, pytest, pytest-asyncio.

**Frontend is out of scope for this plan** — it gets its own plan once this backend runs end-to-end (per the design in `Project.MD`).

---

## Build order

Task numbers below are **stable identifiers, not execution order** — they were assigned when the plan was first written and are kept fixed so cross-references between tasks stay valid. Execute the phases in the order listed here; within a phase, execute tasks in the order listed.

| Phase | Tasks | What you end up with |
|---|---|---|
| 0 — Fixtures | 1 | Invoice PDFs + the policy PDF on disk |
| 1 — Extraction | 2, 3, 4, 5, 6 | `extract_invoice()` working on clean and scanned PDFs |
| 2 — Database | 7, 8, 13, 14 | Postgres running, full schema migrated, vendors seeded |
| 3 — Agent + structured tools | 15, 16, 17, 18, 19, 21, 22, 23 | Agent running end-to-end over **five** tools |
| 4 — Eval harness + **baseline** | 24, 25, 26, 27, 28 | 12 cases scored, `POST /eval/run`, a recorded baseline number |
| 5 — Policy RAG + **re-measure** | 8b, 9, 10, 11, 12, 20, 29 | `search_policy` wired in, same 12 cases re-run and compared |

### Why RAG comes last

The original plan built RAG in Phase 2, before the agent existed. It's been moved to the end deliberately.

Phase 3 builds the agent with only the five structured tools — `lookup_vendor`, `get_invoice_history`, `check_duplicate_invoice`, `get_purchase_order`, `submit_recommendation`. Phase 4 then scores it against all 12 eval cases and records the result. That baseline is expected to be *partial*, and the failures are predictable: cases 1-4 and 10 run entirely off structured tools and should pass, while cases 6, 7, 8, 9, and 11 depend on rules that exist nowhere except the policy document — the 10%/$1,000 lesser-of-two PO tolerance, the PO-required threshold, the currency handling. With no way to read the policy, the agent has to guess at those.

Phase 5 then adds policy retrieval as a single additive change (one more decorated tool in `build_tools`, one sentence appended to the system prompt, the eval cases don't change at all) and re-runs the identical suite.

The point is to make "does RAG actually earn its place here?" a number you measured rather than an assertion you accepted. It also mirrors the capability-eval loop from `AI-Agent-Evals.md`: measure what the agent can do, find where it breaks, make one change, re-measure. If the jump is small, that is a real and useful finding — not a failure.

**Note on already-completed work:** Tasks 7 and 8 (Postgres + the `documents` table) were built before this resequencing, so the `documents` table exists and sits empty until Phase 5. Harmless — no rework needed, just don't expect anything in it before then.

---

## Phase 0 — Invoice fixtures

### Task 1: Source sample invoices + the AP policy PDF

**Files:**
- Create: `backend/fixtures/invoices/clean_acme.pdf`
- Create: `backend/fixtures/invoices/clean_globex.pdf`
- Create: `backend/fixtures/invoices/messy_scanned.pdf`
- Add: `backend/fixtures/policy/FINA_Accounts_Payable.pdf`

This task is manual sourcing, not TDD — there's no test to write first, and no infrastructure needed yet.

**Step 1: Get 2-3 clean, born-digital invoice PDFs**

Search "sample invoice PDF" or generate simple ones (a Google Doc/Word doc with invoice fields, exported to PDF works fine). Write down the ground-truth vendor name, amount, due date, and line items for each one somewhere you'll reference later (Task 27 needs this for the eval cases).

Name vendors so one has a naming variant you'll test later, e.g. one PDF says "Acme Inc" — later you'll seed a vendor record as "ACME Incorporated" so the `lookup_vendor` tool (Task 16) has something real to fuzzy-resolve.

**Step 2: Get one messy/scanned invoice PDF**

Either photograph/scan a printed invoice at an angle, or find a low-quality scanned sample online. This needs to have no usable text layer — it's what exercises the vision fallback in Task 4.

**Step 3: Obtain a real AP policy document**

This is the RAG corpus. Use a genuine published policy rather than a synthetic one — the repo uses `backend/fixtures/policy/FINA_Accounts_Payable.pdf`, UNFPA's *Policy and Procedures on Accounts Payable* (15 pages, ~26,600 characters of extracted text). Any real organization's published AP policy works; many are public.

Two reasons a real document is worth the extra handling over a tidy synthetic one:

1. **It's unambiguously too large to inject into every prompt.** 26k characters settles the "is RAG actually necessary here" question that a 4-bullet policy leaves open.
2. **It's messy in the ways real corpora are messy**, which is the whole point of the exercise. Specifically, this PDF's extracted text contains *zero* `\n\n` paragraph breaks — every line break is a single `\n` from PDF line wrapping. It also carries a table of contents with dot leaders, and a page header (`UNFPA / Policies and Procedures Manual / Policy and Procedures on Accounts Payable`) plus footer (`Effective date: September 2016` and a page number) repeated on all 15 pages. Naive paragraph splitting on `\n\n` returns the entire document as one chunk. Task 9 has to handle all of this.

**Know what rules the document actually contains before writing eval cases.** Survey it first:

```bash
python -c "
import pdfplumber
with pdfplumber.open('fixtures/policy/FINA_Accounts_Payable.pdf') as pdf:
    text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
for kw in ['discrepan', 'duplicat', 'segregation', 'approv', 'currency']:
    print(f'=== {kw} ===')
    for l in text.split('\n'):
        if kw in l.lower() and '....' not in l:
            print('  ', l.strip()[:110])
"
```

The eval cases in Task 27 must test rules this document actually states — a case asserting a rule the corpus doesn't contain is a broken task, not an agent failure.

**Step 4: Commit**

```bash
mkdir -p backend/fixtures/invoices backend/fixtures/policy
git add backend/fixtures/
git commit -m "chore: add sample invoice PDFs and AP policy doc"
```

---

## Phase 1 — Extraction pipeline (no database yet)

Everything in this phase is plain Python operating on files. No Postgres, no FastAPI — those get introduced only once something in a later phase actually needs them.

### Task 2: Repo skeleton + minimal config

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/.env.example`
- Create: `backend/.gitignore`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/tests/__init__.py`
- Test: `backend/tests/test_config.py`

**Step 1: Create the directory structure**

```bash
mkdir -p backend/app backend/tests
touch backend/app/__init__.py backend/tests/__init__.py
```

**Step 2: Write `backend/requirements.txt`** (extraction-only for now — DB and web libraries get added when Phase 2/3 need them)

```
anthropic
pdfplumber
pydantic-settings
pytest
```

**Step 3: Write `backend/.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-...
```

Copy it: `cp backend/.env.example backend/.env` and fill in a real key.

**Step 4: Write `backend/.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
venv/
```

**Step 5: Write the failing test**

```python
# backend/tests/test_config.py
from app.config import Settings

def test_settings_reads_anthropic_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    settings = Settings()
    assert settings.anthropic_api_key == "sk-test"
```

**Step 6: Set up the virtualenv**

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Step 7: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

**Step 8: Write `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()
```

**Step 9: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 10: Commit**

```bash
git add backend/requirements.txt backend/.env.example backend/.gitignore backend/app/ backend/tests/
git commit -m "chore: scaffold backend project with minimal config"
```

---

### Task 3: Text-layer extraction + usability heuristic

**Files:**
- Create: `backend/app/extraction/__init__.py`
- Create: `backend/app/extraction/text_layer.py`
- Test: `backend/tests/test_text_layer.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_text_layer.py
from app.extraction.text_layer import extract_text_layer, is_text_usable

def test_extracts_text_from_clean_pdf():
    text = extract_text_layer("fixtures/invoices/clean_acme.pdf")
    assert len(text) > 50
    assert is_text_usable(text) is True

def test_flags_scanned_pdf_as_unusable():
    text = extract_text_layer("fixtures/invoices/messy_scanned.pdf")
    assert is_text_usable(text) is False

def test_usability_threshold_on_empty_text():
    assert is_text_usable("") is False
    assert is_text_usable("a b") is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_text_layer.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/extraction/text_layer.py`**

```python
import pdfplumber

def extract_text_layer(pdf_path: str) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()

def is_text_usable(text: str, min_length: int = 50, min_words: int = 10) -> bool:
    if len(text) < min_length:
        return False
    if len(text.split()) < min_words:
        return False
    return True
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_text_layer.py -v`
Expected: PASS. If `test_flags_scanned_pdf_as_unusable` fails because your scanned sample actually has an embedded text layer, that's a real signal your fixture isn't messy enough — replace it with one that has no text layer (a pure image scan).

**Step 5: Commit**

```bash
git add backend/app/extraction/__init__.py backend/app/extraction/text_layer.py backend/tests/test_text_layer.py
git commit -m "feat: add PDF text-layer extraction with usability heuristic"
```

---

### Task 4: Vision fallback transcription

**Files:**
- Create: `backend/app/extraction/vision_fallback.py`
- Create: `backend/pytest.ini`
- Test: `backend/tests/test_vision_fallback.py`

**Step 1: Register an `integration` marker for tests that call real external APIs**

```ini
# backend/pytest.ini
[pytest]
markers =
    integration: tests that call a real external API (costs money, needs API keys)
```

**Step 2: Write the failing test**

```python
# backend/tests/test_vision_fallback.py
import pytest
from app.extraction.vision_fallback import transcribe_via_vision

@pytest.mark.integration
def test_transcribes_scanned_invoice():
    text = transcribe_via_vision("fixtures/invoices/messy_scanned.pdf")
    assert len(text) > 50
    # loose check — exact wording from OCR/vision varies
    assert any(word in text.lower() for word in ["invoice", "total", "amount", "due"])
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/test_vision_fallback.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 4: Write `backend/app/extraction/vision_fallback.py`**

```python
import base64
from anthropic import Anthropic
from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)

def transcribe_via_vision(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                },
                {
                    "type": "text",
                    "text": "Transcribe every piece of text visible in this document exactly as written, including handwritten notes. Output plain text only, no commentary.",
                },
            ],
        }],
    )
    return response.content[0].text.strip()
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_vision_fallback.py -v -m integration`
Expected: PASS (requires `ANTHROPIC_API_KEY` set in `.env`)

**Step 6: Commit**

```bash
git add backend/app/extraction/vision_fallback.py backend/tests/test_vision_fallback.py backend/pytest.ini
git commit -m "feat: add vision-based transcription fallback for scanned invoices"
```

---

### Task 5: Structured field extraction

**Files:**
- Create: `backend/app/extraction/fields.py`
- Test: `backend/tests/test_fields.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_fields.py
import pytest
from app.extraction.fields import extract_fields

SAMPLE_TEXT = """
ACME Incorporated
Invoice #INV-1042
Date Due: 2026-09-15
PO Number: PO-88213

Line Items:
Consulting services - $4,500.00
Software license - $1,200.00

Total: $5,700.00
"""

@pytest.mark.integration
def test_extracts_fields_from_text():
    result = extract_fields(SAMPLE_TEXT)
    assert result.vendor_name == "ACME Incorporated"
    assert result.invoice_number == "INV-1042"
    assert result.amount == 5700.00
    assert result.po_number == "PO-88213"
    assert len(result.line_items) == 2
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_fields.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/extraction/fields.py`**

```python
from pydantic import BaseModel
from anthropic import Anthropic
from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)

class LineItemFields(BaseModel):
    description: str
    amount: float

class ExtractedFields(BaseModel):
    vendor_name: str | None
    invoice_number: str | None
    amount: float | None
    due_date: str | None  # ISO 8601, agent/caller parses to date
    po_number: str | None
    line_items: list[LineItemFields]

EXTRACT_TOOL = {
    "name": "record_extracted_fields",
    "description": "Record the structured fields extracted from an invoice's text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor_name": {"type": ["string", "null"]},
            "invoice_number": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "due_date": {"type": ["string", "null"], "description": "ISO 8601 date"},
            "po_number": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["description", "amount"],
                },
            },
        },
        "required": ["vendor_name", "amount", "line_items"],
    },
}

def extract_fields(raw_text: str) -> ExtractedFields:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_extracted_fields"},
        messages=[{
            "role": "user",
            "content": f"Extract the invoice fields from this text:\n\n{raw_text}",
        }],
    )
    tool_call = next(b for b in response.content if b.type == "tool_use")
    return ExtractedFields(**tool_call.input)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_fields.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/extraction/fields.py backend/tests/test_fields.py
git commit -m "feat: add structured field extraction via Claude tool-calling"
```

---

### Task 6: Extraction pipeline orchestrator

**Files:**
- Create: `backend/app/extraction/pipeline.py`
- Test: `backend/tests/test_pipeline.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_pipeline.py
import pytest
from app.extraction.pipeline import extract_invoice

@pytest.mark.integration
def test_pipeline_uses_text_layer_for_clean_pdf():
    result = extract_invoice("fixtures/invoices/clean_acme.pdf")
    assert result.fields.vendor_name is not None
    assert result.used_vision_fallback is False

@pytest.mark.integration
def test_pipeline_falls_back_to_vision_for_scanned_pdf():
    result = extract_invoice("fixtures/invoices/messy_scanned.pdf")
    assert result.used_vision_fallback is True
    assert result.fields.vendor_name is not None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/extraction/pipeline.py`**

```python
from dataclasses import dataclass
from app.extraction.text_layer import extract_text_layer, is_text_usable
from app.extraction.vision_fallback import transcribe_via_vision
from app.extraction.fields import extract_fields, ExtractedFields

@dataclass
class ExtractionResult:
    raw_text: str
    fields: ExtractedFields
    used_vision_fallback: bool

def extract_invoice(pdf_path: str) -> ExtractionResult:
    text = extract_text_layer(pdf_path)
    used_vision_fallback = False

    if not is_text_usable(text):
        text = transcribe_via_vision(pdf_path)
        used_vision_fallback = True

    fields = extract_fields(text)
    return ExtractionResult(raw_text=text, fields=fields, used_vision_fallback=used_vision_fallback)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline.py -v -m integration`
Expected: PASS

**Step 5: Checkpoint — run the pipeline on all three fixtures and read the output**

```bash
python -c "
from app.extraction.pipeline import extract_invoice
for path in ['fixtures/invoices/clean_acme.pdf', 'fixtures/invoices/clean_globex.pdf', 'fixtures/invoices/messy_scanned.pdf']:
    r = extract_invoice(path)
    print(path, '-> vision fallback:', r.used_vision_fallback)
    print(' ', r.fields)
"
```

Compare against the ground truth you wrote down in Task 1. This is the extraction pipeline fully working end-to-end on real messy documents, with zero database involved — everything from here forward builds on top of it.

**Step 6: Commit**

```bash
git add backend/app/extraction/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: wire text-layer/vision/field-extraction into one pipeline"
```

---

## Phase 2 — Database (Tasks 7, 8, then 13, 14)

This is the first point where anything needs to persist, so it's the first point where a database shows up.

**Execute Tasks 7 and 8 here, then jump forward to Tasks 13 and 14** (core schema + vendor seed, further down under the old Phase 3 heading) before starting Phase 3. Tasks 8b through 12 sit physically between them in this document but belong to **Phase 5** — skip past them for now.

### Task 7: Postgres via Docker Compose + DB engine

**Files:**
- Create: `backend/docker-compose.yml`
- Modify: `backend/.env.example`
- Modify: `backend/requirements.txt`
- Modify: `backend/app/config.py`
- Create: `backend/app/db.py`
- Modify: `backend/tests/test_config.py`

**Step 1: Write `backend/docker-compose.yml`**

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: invoice_agent
      POSTGRES_PASSWORD: invoice_agent
      POSTGRES_DB: invoice_agent
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

**Step 2: Start it**

```bash
cd backend
docker compose up -d
```

Expected: `docker compose ps` shows the `db` service healthy on port 5432.

**Step 3: Extend `backend/.env.example`**

```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent
VOYAGE_API_KEY=pa-...
```

Update your real `.env` to match.

**Step 4: Extend `backend/requirements.txt`**

```
sqlalchemy>=2.0
asyncpg
alembic
pgvector
voyageai
pytest-asyncio
```

Re-run `pip install -r requirements.txt`.

**Step 5: Extend the failing test**

```python
# backend/tests/test_config.py — add this test to the existing file
def test_settings_reads_database_and_voyage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
    assert settings.voyage_api_key == "pa-test"
```

**Step 6: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `Settings` has no field `database_url`

**Step 7: Extend `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str
    database_url: str
    voyage_api_key: str

    class Config:
        env_file = ".env"

settings = Settings()
```

**Step 8: Write `backend/app/db.py`**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
```

**Step 9: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 10: Commit**

```bash
git add backend/docker-compose.yml backend/.env.example backend/requirements.txt backend/app/config.py backend/app/db.py backend/tests/test_config.py
git commit -m "feat: add Postgres/pgvector via Docker Compose and async DB engine"
```

---

### Task 8: `documents` model + first migration

**Files:**
- Create: `backend/app/models.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_documents.py`

**Step 1: Write `backend/app/models.py`** — just `Document` for now. `invoice_id` is a plain UUID column with no foreign key yet, since `invoices` doesn't exist until Task 13 — the FK constraint gets added then, once there's a table for it to reference.

```python
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Text
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase):
    pass

def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    section: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "## 2. Approval Authority"
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
```

> **Revised after the RAG-scope decision.** This model originally had `invoice_id` and `doc_type` columns, because the plan was to embed invoice text alongside the policy. That was dropped — see "RAG is policy-only" in `Project.MD`. The corpus is now only the policy, so `doc_type` would be a constant and `invoice_id` would always be null. They're replaced by `section`, which carries the policy heading a chunk came from so retrieved clauses are citable.
>
> If you already ran the original migration, the table needs altering rather than creating — see Task 8b.

**Step 2: Initialize Alembic**

```bash
cd backend
alembic init alembic
```

**Step 3: Edit `backend/alembic/env.py`** — replace the `target_metadata = None` line and wire in the async engine:

```python
# add near the top, after existing imports
import asyncio
from app.models import Base
from app.config import settings

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)

# replace run_migrations_online with:
from sqlalchemy.ext.asyncio import async_engine_from_config

def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

asyncio.run(run_migrations_online())
```

**Step 4: Enable the pgvector extension before the first migration**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Step 5: Generate and run the migration**

```bash
alembic revision --autogenerate -m "add documents table"
alembic upgrade head
```

Expected: a new file under `backend/alembic/versions/`, and `alembic upgrade head` completes without error.

**Step 6: Verify the table exists**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c "\dt"
```

Expected: `documents`.

**Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic.ini backend/alembic/
git commit -m "feat: add Document model and initial migration"
```

---

## Phase 5 — Policy RAG and re-measurement (Tasks 8b, 9, 10, 11, 12, 20, 29)

> **These tasks run LAST, after Phase 4's baseline is recorded.** They appear here in the document only because the task numbers predate the resequencing. Do not build them before the eval baseline exists — the entire point is to measure the difference they make. Skip ahead to Task 13 if you're following the build order.

---

### Task 8b: Migrate `documents` to the policy-only shape

Only needed if you already ran Task 8's original migration (with `invoice_id`/`doc_type`). If you're building fresh from the revised model above, skip this.

**Files:**
- Modify: `backend/app/models.py` (already done above)
- Create: `backend/alembic/versions/000X_documents_policy_only.py`

**Step 1: Generate the migration**

```bash
alembic revision --autogenerate -m "documents: drop invoice_id/doc_type, add section"
```

**Step 2: Check the generated file** — autogenerate should produce `op.drop_column` for `invoice_id` and `doc_type`, plus `op.add_column` for `section`. Because `section` is `nullable=False` and the table may already hold rows, add a server default in the migration or truncate first. The table is expected to be empty at this point, so truncating is simplest:

```python
def upgrade() -> None:
    op.execute("TRUNCATE TABLE documents")
    op.add_column('documents', sa.Column('section', sa.Text(), nullable=False))
    op.drop_column('documents', 'invoice_id')
    op.drop_column('documents', 'doc_type')
```

**Step 3: Apply and verify**

```bash
alembic upgrade head
docker compose exec db psql -U invoice_agent -d invoice_agent -c "\d documents"
```

Expected columns: `id`, `section`, `chunk_text`, `embedding`.

**Step 4: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/
git commit -m "refactor: narrow documents table to policy-only RAG corpus"
```

---

### Task 9: Chunking the policy PDF

Only the policy gets chunked — invoice text is not embedded (see the RAG-scope note in Task 8). So there is one chunking function, not two.

**Why not paragraph splitting.** The obvious approach — split on `\n\n`, one chunk per paragraph — does not work on this input, because PDF text extraction produces no blank lines at all. Every line ends in a single `\n` from line wrapping, so `text.split("\n\n")` returns a one-element list containing the entire 26k-character document. Verify this yourself before writing the chunker; it's the kind of assumption that fails silently rather than loudly:

```bash
python -c "
import pdfplumber
with pdfplumber.open('fixtures/policy/FINA_Accounts_Payable.pdf') as pdf:
    text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
print('paragraph breaks found:', text.count('\n\n'))
"
```

**What to do instead — split on section headings.** The document has a real hierarchy expressed as numbered headings, which survive extraction intact: roman numerals (`I. Purpose`, `II. Policy`), letters (`A. Segregation of duties`), and `Step N:` markers. Detect those with a regex, treat each as a section boundary, and join the wrapped lines between boundaries back into flowing text. Each chunk then carries its heading, which makes retrieved clauses citable — the agent can say "per §III.A Step 1" and the groundedness grader can check that.

**Three pieces of cleanup the real document requires:**

- **Page furniture**: the header (`UNFPA`, `Policies and Procedures Manual`, `Policy and Procedures on Accounts Payable`) and footer (`Effective date: September 2016`, bare page numbers) repeat on all 15 pages. Left in, they'd be embedded ~15 times each and pollute every similarity search with boilerplate.
- **Table of contents**: TOC entries look exactly like headings but are followed by dot leaders (`.......... 7`). Without filtering, every section gets a duplicate empty chunk from its TOC line.
- **Long sections**: some sections run past 2,000 characters. Sub-split those on sentence boundaries so a chunk is one idea, not one chapter.

**Files:**
- Create: `backend/app/rag/__init__.py`
- Create: `backend/app/rag/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_chunking.py
from app.rag.chunking import chunk_policy_text, PolicyChunk

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
    chunks = chunk_policy_text(SAMPLE)
    sections = [c.section for c in chunks]
    assert "I. Purpose" in sections
    assert "II. Policy" in sections


def test_drops_table_of_contents_entries():
    chunks = chunk_policy_text(SAMPLE)
    # TOC lines have dot leaders; none of their text should survive
    assert not any("....." in c.text for c in chunks)
    # "I. Purpose" appears twice in the source (TOC + body) but is one section
    assert sum(1 for c in chunks if c.section == "I. Purpose") == 1


def test_strips_repeating_page_furniture():
    chunks = chunk_policy_text(SAMPLE)
    body = " ".join(c.text for c in chunks)
    assert "Policies and Procedures Manual" not in body
    assert "Effective date: September 2016" not in body


def test_rejoins_wrapped_lines_into_flowing_text():
    chunks = chunk_policy_text(SAMPLE)
    policy = next(c for c in chunks if c.section == "II. Policy")
    assert "greater than 10 percent or $1,000 USD must be resolved" in policy.text


def test_embed_text_includes_section_heading():
    chunks = chunk_policy_text(SAMPLE)
    policy = next(c for c in chunks if c.section == "II. Policy")
    assert policy.embed_text.startswith("II. Policy")
    assert "10 percent" in policy.embed_text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_chunking.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/rag/chunking.py`**

```python
import re
from dataclasses import dataclass

HEADING_RE = re.compile(
    r"^(?:(?:[IVX]+|[A-H])\.\s+\S|Step\s+\d+\s*:)"
)

# Repeating header/footer lines that appear on every page of the source PDF.
PAGE_FURNITURE = {
    "UNFPA",
    "Policies and Procedures Manual",
    "Policy and Procedures on Accounts Payable",
}

MAX_CHUNK_CHARS = 1500


@dataclass
class PolicyChunk:
    section: str  # heading this chunk lives under, e.g. "II. Policy"
    text: str     # the rule text itself

    @property
    def embed_text(self) -> str:
        """What gets embedded: heading + body, so the vector captures which
        part of the policy the rule belongs to, not just its wording."""
        return f"{self.section}\n{self.text}"


def _is_noise(line: str) -> bool:
    if not line:
        return True
    if line in PAGE_FURNITURE:
        return True
    if line.isdigit():                      # bare page number
        return True
    if line.startswith("Effective date:"):
        return True
    if "....." in line:                     # table-of-contents entry
        return True
    return False


def _split_long(section: str, body: str) -> list[PolicyChunk]:
    """Sub-split an over-long section on sentence boundaries."""
    if len(body) <= MAX_CHUNK_CHARS:
        return [PolicyChunk(section=section, text=body)]

    sentences = re.split(r"(?<=[.:])\s+", body)
    chunks, current = [], ""
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
        # lines before the first heading are front matter -- dropped

    chunks: list[PolicyChunk] = []
    for section, lines in sections:
        body = " ".join(lines).strip()
        if not body:
            continue  # heading with no content (e.g. a leftover TOC duplicate)
        chunks.extend(_split_long(section, body))
    return chunks
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_chunking.py -v`
Expected: PASS

**Step 5: Sanity-check against the real 15-page PDF**

```bash
python -c "
import pdfplumber
from app.rag.chunking import chunk_policy_text
with pdfplumber.open('fixtures/policy/FINA_Accounts_Payable.pdf') as pdf:
    text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
chunks = chunk_policy_text(text)
print(len(chunks), 'chunks')
for c in chunks:
    print(f'  [{c.section[:38]:38}] {len(c.text):5} chars | {c.text[:60]}...')
"
```

Read the output rather than just checking it runs. Every chunk should be a coherent, self-contained rule under the right heading. Watch for: boilerplate that survived the noise filter, chunks that are only a fragment of a sentence, or a heading whose body swallowed the *next* section's content (which would mean the heading regex missed a boundary).

**Step 6: Commit**

```bash
git add backend/app/rag/__init__.py backend/app/rag/chunking.py backend/tests/test_chunking.py
git commit -m "feat: add section-aware chunking for the policy PDF"
```

---

### Task 10: Voyage embeddings client

**Files:**
- Create: `backend/app/rag/embeddings.py`
- Test: `backend/tests/test_embeddings.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_embeddings.py
import pytest
from app.rag.embeddings import embed_texts

@pytest.mark.integration
def test_embed_texts_returns_vectors_of_expected_dimension():
    vectors = embed_texts(["hello world", "invoice total due"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_embeddings.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/rag/embeddings.py`**

```python
import voyageai
from app.config import settings

client = voyageai.Client(api_key=settings.voyage_api_key)

def embed_texts(texts: list[str], input_type: str = "document") -> list[list[float]]:
    result = client.embed(texts, model="voyage-3-lite", input_type=input_type)
    return result.embeddings
```

Note: `input_type="document"` when embedding chunks to store, `input_type="query"` when embedding a search query in Task 12 — Voyage tunes the embedding differently for each.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_embeddings.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/rag/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat: add Voyage AI embeddings client wrapper"
```

---

### Task 11: Store chunks in pgvector

**Files:**
- Create: `backend/app/rag/store.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_store.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_store.py
import pytest
from sqlalchemy import select
from app.rag.store import store_document_chunks
from app.models import Document

@pytest.mark.integration
@pytest.mark.asyncio
async def test_store_policy_chunks(db_session):
    chunks = [
        PolicyChunk(section="II. Policy", text="Discrepancies greater than 10 percent must be resolved."),
        PolicyChunk(section="IV. Other", text="There must be appropriate segregation of duties."),
    ]
    await store_policy_chunks(db_session, chunks)
    rows = (await db_session.execute(select(Document))).scalars().all()
    assert len(rows) == 2
    assert len(rows[0].embedding) == 1024
    assert {r.section for r in rows} == {"II. Policy", "IV. Other"}
```

Note what gets embedded: `chunk.embed_text` (heading + body), not `chunk.text` alone. A query like "what's the tolerance for purchase order discrepancies" should match a chunk partly because it lives under a policy heading, not only because of its wording. The `chunk_text` column stores the body on its own, since that's what gets shown back to the agent.

**Step 2: Write `backend/tests/conftest.py`** — this `db_session` fixture is reused by every DB-touching test from here on:

```python
import pytest_asyncio
from app.db import SessionLocal

@pytest_asyncio.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session
        await session.rollback()
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/test_store.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 4: Write `backend/app/rag/store.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Document
from app.rag.chunking import PolicyChunk
from app.rag.embeddings import embed_texts

async def store_policy_chunks(
    session: AsyncSession, chunks: list[PolicyChunk]
) -> list[Document]:
    # embed heading + body, but store the body alone for display
    embeddings = embed_texts([c.embed_text for c in chunks], input_type="document")
    documents = [
        Document(section=chunk.section, chunk_text=chunk.text, embedding=embedding)
        for chunk, embedding in zip(chunks, embeddings)
    ]
    session.add_all(documents)
    await session.commit()
    return documents
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_store.py -v -m integration`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/rag/store.py backend/tests/test_store.py backend/tests/conftest.py
git commit -m "feat: store document chunks with embeddings in pgvector"
```

---

### Task 12: Similarity search over the policy

**Files:**
- Create: `backend/app/rag/search.py`
- Test: `backend/tests/test_search.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_search.py
import pytest
from app.rag.chunking import PolicyChunk
from app.rag.store import store_policy_chunks
from app.rag.search import search_policy

@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_returns_most_relevant_clause(db_session):
    await store_policy_chunks(db_session, [
        PolicyChunk(section="IV. Other",
                    text="There must be appropriate segregation of functional responsibilities."),
        PolicyChunk(section="II. Policy",
                    text="Discrepancies between the vendor invoice and the purchase order greater "
                         "than 10 percent or $1,000 USD (the lesser of the two) must be resolved."),
    ])
    results = await search_policy(db_session, query="how much can an invoice differ from its PO", top_k=1)
    assert "10 percent" in results[0].chunk_text
    assert results[0].section == "II. Policy"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_search.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/rag/search.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Document
from app.rag.embeddings import embed_texts

async def search_policy(
    session: AsyncSession, query: str, top_k: int = 5
) -> list[Document]:
    # input_type="query" matters -- Voyage embeds queries and documents
    # differently, and mixing them measurably degrades retrieval quality.
    query_embedding = embed_texts([query], input_type="query")[0]
    stmt = (
        select(Document)
        .order_by(Document.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    result = await session.execute(stmt)
    return result.scalars().all()
```

No filtering clause is needed: the `documents` table holds only policy chunks (see the RAG-scope note in Task 8).

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_search.py -v -m integration`
Expected: PASS

**Step 5: Checkpoint — load the real 15-page policy and query it**

```bash
python -c "
import asyncio, pdfplumber
from app.db import SessionLocal
from app.rag.chunking import chunk_policy_text
from app.rag.store import store_policy_chunks
from app.rag.search import search_policy

QUERIES = [
    'how much can an invoice differ from the purchase order',
    'who has to approve a payment before it is released',
    'what if the invoice is in a different currency',
    'can the same person request and approve a payment',
]

async def main():
    with pdfplumber.open('fixtures/policy/FINA_Accounts_Payable.pdf') as pdf:
        text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    chunks = chunk_policy_text(text)
    async with SessionLocal() as session:
        await store_policy_chunks(session, chunks)
        for q in QUERIES:
            print(f'\n--- {q}')
            for r in await search_policy(session, query=q, top_k=2):
                print(f'  [{r.section}] {r.chunk_text[:110]}...')

asyncio.run(main())
"
```

Read the results, don't just check it ran. The PO-tolerance query should surface the §II clause containing "10 percent or $1,000 USD"; the segregation question should surface §IV.A. If a query returns page boilerplate or an unrelated section, that's a chunking problem to fix in Task 9, not something to work around here.

**Step 6: Commit**

```bash
git add backend/app/rag/search.py backend/tests/test_search.py
git commit -m "feat: add pgvector similarity search over the policy corpus"
```

---

## Phases 2 (cont.), 3, and 4 — Schema, agent, structured tools, eval harness

This is where the rest of the schema shows up — vendors, invoices, line items, purchase orders, and the tables the agent's tools and the eval harness both read and write.

Task sequencing across this stretch of the document:

- **Tasks 13-14** finish **Phase 2** — core schema and seeded vendors.
- **Tasks 15-19, then 21-23** are **Phase 3** — the agent and its five structured tools. **Skip Task 20**; `search_policy` belongs to Phase 5, after the baseline exists. Go 19 → 21.
- **Tasks 24-28** are **Phase 4** — the eval harness, graders, the 12 cases, and the baseline run.

### Task 13: Core schema (vendors, invoices, line items, agent runs, audit log)

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0002_core_schema.py`

**Step 1: Extend `backend/app/models.py`** — add these models, and update `Document.invoice_id` to become a real foreign key now that `invoices` exists:

```python
# add these imports at the top
from datetime import datetime, date
from sqlalchemy import String, Numeric, Date, DateTime, ForeignKey, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

# change Document.invoice_id from a plain UUID column to:
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=True)

# then append these new models:
class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    bank_details: Mapped[str] = mapped_column(String, nullable=True)

class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    invoice_number: Mapped[str] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)
    due_date: Mapped[date] = mapped_column(Date, nullable=True)
    po_number: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    raw_pdf_path: Mapped[str] = mapped_column(String, nullable=False)
    raw_text: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line_items: Mapped[list["LineItem"]] = relationship(back_populates="invoice")

class LineItem(Base):
    __tablename__ = "line_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="live")  # "live" | "eval"
    transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # "agent" | "human"
    action: Mapped[str] = mapped_column(String, nullable=False)
    before_state: Mapped[dict] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

**Step 2: Add `CONFIDENCE_ESCALATION_THRESHOLD` to config** — extend `backend/.env.example`:

```
CONFIDENCE_ESCALATION_THRESHOLD=0.7
```

Extend `backend/app/config.py`:

```python
class Settings(BaseSettings):
    anthropic_api_key: str
    database_url: str
    voyage_api_key: str
    confidence_escalation_threshold: float = 0.7

    class Config:
        env_file = ".env"
```

**Step 3: Enable `pg_trgm`, needed by `lookup_vendor` in Task 16**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

**Step 4: Generate and run the migration**

```bash
alembic revision --autogenerate -m "add core schema"
alembic upgrade head
```

Expected: the autogenerated migration adds `vendors`, `invoices`, `line_items`, `agent_runs`, `audit_log`, and a foreign key constraint on `documents.invoice_id`.

**Step 5: Verify**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c "\dt"
```

Expected: `vendors`, `invoices`, `line_items`, `documents`, `agent_runs`, `audit_log`.

**Step 6: Commit**

```bash
git add backend/app/models.py backend/app/config.py backend/.env.example backend/alembic/versions/
git commit -m "feat: add core schema (vendors, invoices, line_items, agent_runs, audit_log)"
```

---

### Task 14: Seed vendor data

**Files:**
- Create: `backend/fixtures/seed_vendors.py`

**Step 1: Write the seed script**

```python
# backend/fixtures/seed_vendors.py
import asyncio
from app.db import SessionLocal
from app.models import Vendor

VENDORS = [
    {"name": "ACME Incorporated", "normalized_name": "acme incorporated", "bank_details": "IBAN GB00ACME00000000000001"},
    {"name": "Globex Corp", "normalized_name": "globex corp", "bank_details": "IBAN GB00GLBX00000000000002"},
]

async def seed():
    async with SessionLocal() as session:
        for v in VENDORS:
            session.add(Vendor(**v))
        await session.commit()

if __name__ == "__main__":
    asyncio.run(seed())
```

**Step 2: Run it**

```bash
cd backend && python -m fixtures.seed_vendors
```

**Step 3: Commit**

```bash
git add backend/fixtures/seed_vendors.py
git commit -m "chore: add vendor seed script"
```

---

### Task 15: Agent run transcript recording

**Files:**
- Create: `backend/app/agent/__init__.py`
- Create: `backend/app/agent/transcript.py`
- Test: `backend/tests/test_transcript.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_transcript.py
import pytest
from sqlalchemy import select
from app.agent.transcript import RunTranscript
from app.models import AgentRun

@pytest.mark.asyncio
async def test_transcript_records_tool_calls_and_saves(db_session, seeded_invoice):
    t = RunTranscript(invoice_id=seeded_invoice.id, source="eval")
    t.record_tool_call("lookup_vendor", {"vendor_name": "Acme"}, {"matched": True})
    t.record_final(decision="approve", confidence=0.9, reasoning="Vendor matched, amount normal.")
    await t.save(db_session)

    result = await db_session.execute(select(AgentRun).where(AgentRun.invoice_id == seeded_invoice.id))
    row = result.scalar_one()
    assert row.decision == "approve"
    assert row.transcript["tool_calls"][0]["tool"] == "lookup_vendor"
```

Add a `seeded_invoice` fixture to `backend/tests/conftest.py`:

```python
@pytest_asyncio.fixture
async def seeded_invoice(db_session):
    from app.models import Invoice
    invoice = Invoice(raw_pdf_path="fixtures/invoices/clean_acme.pdf", amount=5700.00, status="pending")
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)
    return invoice
```

(Also add `import pytest_asyncio` at the top of `conftest.py` if it isn't already imported there.)

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_transcript.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/agent/transcript.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AgentRun

class RunTranscript:
    def __init__(self, invoice_id, source: str = "live"):
        self.invoice_id = invoice_id
        self.source = source
        self.tool_calls: list[dict] = []
        self.decision: str | None = None
        self.confidence: float | None = None
        self.reasoning: str | None = None

    def record_tool_call(self, tool: str, input: dict, output) -> None:
        self.tool_calls.append({"tool": tool, "input": input, "output": output})

    def record_final(self, decision: str, confidence: float, reasoning: str) -> None:
        self.decision = decision
        self.confidence = confidence
        self.reasoning = reasoning

    async def save(self, session: AsyncSession) -> AgentRun:
        run = AgentRun(
            invoice_id=self.invoice_id,
            source=self.source,
            transcript={"tool_calls": self.tool_calls, "reasoning": self.reasoning},
            decision=self.decision,
            confidence=self.confidence,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_transcript.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/__init__.py backend/app/agent/transcript.py backend/tests/test_transcript.py backend/tests/conftest.py
git commit -m "feat: add agent run transcript recording"
```

---

### Task 16: Tool — `lookup_vendor` (backs eval cases 4 and 5)

**Files:**
- Create: `backend/app/agent/tools.py`
- Test: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_tools.py
import pytest
from app.agent.tools import lookup_vendor

@pytest.mark.asyncio
async def test_lookup_vendor_resolves_close_name_match(db_session, seeded_vendor):
    # seeded_vendor.name == "ACME Incorporated"
    result = await lookup_vendor(db_session, vendor_name="Acme Inc")
    assert result["matched"] is True
    assert result["vendor_id"] == str(seeded_vendor.id)

@pytest.mark.asyncio
async def test_lookup_vendor_no_match_below_threshold(db_session, seeded_vendor):
    result = await lookup_vendor(db_session, vendor_name="Completely Different Co")
    assert result["matched"] is False
```

Add `seeded_vendor` fixture to `conftest.py`:

```python
@pytest_asyncio.fixture
async def seeded_vendor(db_session):
    from app.models import Vendor
    vendor = Vendor(name="ACME Incorporated", normalized_name="acme incorporated", bank_details="IBAN GB00ACME00000000000001")
    db_session.add(vendor)
    await db_session.commit()
    await db_session.refresh(vendor)
    return vendor
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py::test_lookup_vendor_resolves_close_name_match -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/agent/tools.py`** (start the file; more tools get appended in later tasks)

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SIMILARITY_THRESHOLD = 0.4

async def lookup_vendor(session: AsyncSession, vendor_name: str) -> dict:
    result = await session.execute(
        text("""
            SELECT id, name, bank_details, similarity(normalized_name, :name) AS sim
            FROM vendors
            WHERE similarity(normalized_name, :name) > :threshold
            ORDER BY sim DESC
            LIMIT 1
        """),
        {"name": vendor_name.lower(), "threshold": SIMILARITY_THRESHOLD},
    )
    row = result.first()
    if row is None:
        return {"matched": False}
    return {
        "matched": True,
        "vendor_id": str(row.id),
        "vendor_name": row.name,
        "bank_details": row.bank_details,
        "similarity": round(row.sim, 2),
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k lookup_vendor`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py backend/tests/conftest.py
git commit -m "feat: add lookup_vendor tool with pg_trgm fuzzy matching"
```

---

### Task 17: Tool — `get_invoice_history`

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_get_invoice_history_returns_summary_stats(db_session, seeded_vendor, three_past_invoices):
    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id), lookback_days=365)
    assert result["count"] == 3
    assert result["average_amount"] == pytest.approx(1000.0)

@pytest.mark.asyncio
async def test_get_invoice_history_empty_for_new_vendor(db_session, seeded_vendor):
    result = await get_invoice_history(db_session, vendor_id=str(seeded_vendor.id), lookback_days=365)
    assert result["count"] == 0
```

Add `three_past_invoices` fixture:

```python
@pytest_asyncio.fixture
async def three_past_invoices(db_session, seeded_vendor):
    from app.models import Invoice
    for amount in [900.0, 1000.0, 1100.0]:
        db_session.add(Invoice(vendor_id=seeded_vendor.id, amount=amount, status="approved", raw_pdf_path="x.pdf"))
    await db_session.commit()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k get_invoice_history`
Expected: FAIL — `get_invoice_history` not defined

**Step 3: Append to `backend/app/agent/tools.py`**

```python
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.models import Invoice

async def get_invoice_history(session: AsyncSession, vendor_id: str, lookback_days: int = 365) -> dict:
    since = datetime.utcnow() - timedelta(days=lookback_days)
    result = await session.execute(
        select(func.count(Invoice.id), func.avg(Invoice.amount), func.max(Invoice.created_at))
        .where(Invoice.vendor_id == vendor_id, Invoice.created_at >= since)
    )
    count, avg_amount, most_recent = result.one()
    return {
        "count": count or 0,
        "average_amount": float(avg_amount) if avg_amount else None,
        "most_recent_date": most_recent.isoformat() if most_recent else None,
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k get_invoice_history`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py backend/tests/conftest.py
git commit -m "feat: add get_invoice_history tool"
```

---

### Task 18: Tool — `check_duplicate_invoice`

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_check_duplicate_invoice_finds_match(db_session, seeded_vendor):
    from app.models import Invoice
    db_session.add(Invoice(vendor_id=seeded_vendor.id, amount=500.0, invoice_number="INV-1", status="approved", raw_pdf_path="x.pdf"))
    await db_session.commit()

    result = await check_duplicate_invoice(db_session, vendor_id=str(seeded_vendor.id), amount=500.0, invoice_number="INV-1")
    assert result["is_duplicate"] is True

@pytest.mark.asyncio
async def test_check_duplicate_invoice_no_match(db_session, seeded_vendor):
    result = await check_duplicate_invoice(db_session, vendor_id=str(seeded_vendor.id), amount=500.0, invoice_number="INV-999")
    assert result["is_duplicate"] is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k duplicate`
Expected: FAIL — not defined

**Step 3: Append to `backend/app/agent/tools.py`**

```python
async def check_duplicate_invoice(session: AsyncSession, vendor_id: str, amount: float, invoice_number: str | None) -> dict:
    stmt = select(Invoice).where(Invoice.vendor_id == vendor_id, Invoice.amount == amount)
    if invoice_number:
        stmt = stmt.where(Invoice.invoice_number == invoice_number)
    result = await session.execute(stmt)
    match = result.first()
    return {"is_duplicate": match is not None}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k duplicate`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: add check_duplicate_invoice tool"
```

---

### Task 19: Tool — `get_purchase_order`

**Files:**
- Modify: `backend/app/models.py` — add a minimal `PurchaseOrder` table (this project fakes a PO system rather than integrating a real one)
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`
- Create: `backend/alembic/versions/0003_purchase_orders.py`

**Step 1: Add the model**

```python
# append to backend/app/models.py
class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = uuid_pk()
    po_number: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
```

Generate and apply the migration:

```bash
alembic revision --autogenerate -m "add purchase_orders table"
alembic upgrade head
```

**Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_get_purchase_order_matches_amount(db_session, seeded_po):
    result = await get_purchase_order(db_session, po_number="PO-88213")
    assert result["exists"] is True
    assert result["amount"] == 5700.00

@pytest.mark.asyncio
async def test_get_purchase_order_not_found(db_session):
    result = await get_purchase_order(db_session, po_number="PO-DOES-NOT-EXIST")
    assert result["exists"] is False
```

Add fixture:

```python
@pytest_asyncio.fixture
async def seeded_po(db_session):
    from app.models import PurchaseOrder
    po = PurchaseOrder(po_number="PO-88213", amount=5700.00)
    db_session.add(po)
    await db_session.commit()
    return po
```

**Step 3: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k purchase_order`
Expected: FAIL — not defined

**Step 4: Append to `backend/app/agent/tools.py`**

```python
from app.models import PurchaseOrder

async def get_purchase_order(session: AsyncSession, po_number: str) -> dict:
    result = await session.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
    po = result.scalar_one_or_none()
    if po is None:
        return {"exists": False}
    return {"exists": True, "amount": float(po.amount)}
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k purchase_order`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/models.py backend/app/agent/tools.py backend/tests/test_tools.py backend/tests/conftest.py backend/alembic/versions/
git commit -m "feat: add purchase_orders table and get_purchase_order tool"
```

---

### Task 20: Tool — `search_policy` (wraps RAG) — **PHASE 5**

> Do not build this during Phase 3. It runs after Task 28's baseline is recorded. Skip from Task 19 to Task 21.

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_policy_tool_finds_relevant_clause(db_session):
    from app.rag.chunking import PolicyChunk
    from app.rag.store import store_policy_chunks
    await store_policy_chunks(db_session, [
        PolicyChunk(section="II. Policy",
                    text="Discrepancies between the vendor invoice and the purchase order greater "
                         "than 10 percent or $1,000 USD (the lesser of the two) must be resolved."),
    ])
    result = await search_policy_tool(db_session, query="how much can an invoice differ from its PO")
    assert any("10 percent" in r["text"] for r in result["clauses"])
    assert result["clauses"][0]["section"] == "II. Policy"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k search_policy -m integration`
Expected: FAIL — not defined

**Step 3: Append to `backend/app/agent/tools.py`**

```python
from app.rag.search import search_policy

async def search_policy_tool(session: AsyncSession, query: str) -> dict:
    docs = await search_policy(session, query=query, top_k=5)
    return {"clauses": [{"section": d.section, "text": d.chunk_text} for d in docs]}
```

Two design notes, both following the "writing tools for agents" principles the project committed to:

- Named `search_policy_tool` (not `search_policy`) to avoid clashing with the RAG-layer function it wraps.
- Each result carries its `section`, not just the text. That's what lets the agent cite "per §II" in its reasoning and lets the groundedness grader verify the citation — a bare wall of text would make both impossible.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k search_policy -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: add search_policy agent tool wrapping RAG similarity search"
```

---

### Task 21: Tool — `submit_recommendation` + confidence escalation

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
def test_submit_recommendation_passes_through_above_threshold():
    result = submit_recommendation(decision="approve", confidence=0.9, reasoning="all checks passed")
    assert result["final_decision"] == "approve"

def test_submit_recommendation_forces_escalation_below_threshold():
    result = submit_recommendation(decision="approve", confidence=0.4, reasoning="looks fine but unsure")
    assert result["final_decision"] == "escalate"
    assert result["original_decision"] == "approve"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k submit_recommendation`
Expected: FAIL — not defined

**Step 3: Append to `backend/app/agent/tools.py`**

```python
from app.config import settings

def submit_recommendation(decision: str, confidence: float, reasoning: str) -> dict:
    final_decision = decision
    if confidence < settings.confidence_escalation_threshold and decision != "escalate":
        final_decision = "escalate"
    return {
        "original_decision": decision,
        "final_decision": final_decision,
        "confidence": confidence,
        "reasoning": reasoning,
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k submit_recommendation`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: add submit_recommendation tool with server-side confidence escalation"
```

---

### Task 22: Wire the tools into the Tool Runner

The SDK's Tool Runner drives the request → execute → loop cycle, so there is no hand-written `while` loop, no `TOOL_DEFINITIONS` array of JSON schemas, and no name→function dispatch table. Those three things are what the runner replaces.

**The one design constraint that shapes this task.** The runner derives each tool's JSON schema from its **Python signature and docstring**, so a tool's parameters must be exactly what Claude should supply. Our tool functions from Tasks 16-21 take a `session` (and need to write to a transcript) — neither of which Claude should ever see or invent. The fix is a thin wrapper layer: keep the plain functions exactly as built (they stay unit-testable against `db_session`), and wrap each in a decorated closure that binds `session` and `transcript` invisibly.

That split is worth internalizing — it's the general answer to "my tool needs context the model shouldn't control."

**Docstrings are now load-bearing.** With hand-written schemas the `description` field was obviously part of the API contract. Under the runner, the docstring *is* the description Claude reads to decide whether to call the tool, and each `Args:` line is a parameter description. A vague docstring is a silently worse tool. Per the tool-design guidance, be prescriptive about **when** to call it, not just what it does.

**Files:**
- Create: `backend/app/agent/prompts.py`
- Create: `backend/app/agent/runner.py`
- Test: `backend/tests/test_runner.py`

**Step 1: Write the failing test**

The manual loop could be tested by monkeypatching `messages.create` and feeding it fake responses. The runner owns that loop internally, so there's no seam to mock without reaching into SDK internals — which would test the mock, not the code. Test what's actually ours instead: that the tool set is built correctly and bound to the session.

```python
# backend/tests/test_runner.py
import pytest
from app.agent.runner import build_tools
from app.agent.transcript import RunTranscript


@pytest.mark.asyncio
async def test_build_tools_exposes_the_five_structured_tools(db_session, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = build_tools(db_session, transcript, seeded_invoice)
    names = {t.name for t in tools}
    assert names == {
        "lookup_vendor",
        "get_invoice_history",
        "check_duplicate_invoice",
        "get_purchase_order",
        "submit_recommendation",
    }


@pytest.mark.asyncio
async def test_tools_hide_session_from_the_model_schema(db_session, seeded_invoice):
    """Claude must never be asked to supply a DB session or a transcript."""
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    for tool in build_tools(db_session, transcript, seeded_invoice):
        params = tool.input_schema.get("properties", {})
        assert "session" not in params
        assert "transcript" not in params


@pytest.mark.asyncio
async def test_calling_a_tool_records_it_on_the_transcript(db_session, seeded_vendor, seeded_invoice):
    transcript = RunTranscript(invoice_id=seeded_invoice.id)
    tools = {t.name: t for t in build_tools(db_session, transcript, seeded_invoice)}
    await tools["lookup_vendor"].call({"vendor_name": "Acme Inc"})
    assert transcript.tool_calls[0]["tool"] == "lookup_vendor"
    assert transcript.tool_calls[0]["output"]["matched"] is True
```

The exact attribute names for reading a built tool's schema (`.name`, `.input_schema`) and invoking it directly (`.call(...)`) are SDK surface — if the first run errors on an attribute, read the error and adjust rather than guessing a second time. The *assertions* are the point: no `session` in the schema, and calling a tool lands on the transcript.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/agent/prompts.py`**

Write the **Phase 3 version** now — it must not mention policy retrieval, since that tool won't exist until Phase 5. A prompt that tells the agent to consult a policy it cannot read would contaminate the baseline.

```python
SYSTEM_PROMPT = """You are an accounts-payable review agent. Given an invoice's extracted fields, \
investigate it using the tools available before making a recommendation. Always check the vendor \
resolves correctly, check invoice history for anomalies, check for duplicates, and check any \
referenced purchase order. When you have enough information, call submit_recommendation with \
your decision (approve, reject, or escalate), a confidence score from 0 to 1, and your reasoning."""
```

**In Phase 5**, after the baseline is recorded, append one sentence — and nothing else, so the only variable that changed is policy access:

```python
# appended to SYSTEM_PROMPT in Phase 5, alongside adding search_policy_tool
"""Use search_policy_tool to look up the written AP policy governing this invoice, \
and cite the section you relied on in your reasoning. Do not assert a rule you have \
not retrieved."""
```

**Step 4: Write `backend/app/agent/runner.py`**

```python
import json

from anthropic import AsyncAnthropic, beta_async_tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.transcript import RunTranscript
from app.agent import tools as tool_impls
from app.models import Invoice

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

MAX_ITERATIONS = 8


def build_tools(session: AsyncSession, transcript: RunTranscript, invoice: Invoice) -> list:
    """Build the agent's tool set for one invoice review.

    Each tool is a closure over `session`, `transcript`, and `invoice` so that
    none of them appear in the schema Claude sees -- the runner derives that
    schema from the signature, and the model must only ever supply values it
    can legitimately know. Recording to the transcript happens here rather than
    in the loop, since the runner owns the loop.
    """

    @beta_async_tool
    async def lookup_vendor(vendor_name: str) -> str:
        """Resolve a vendor name against the vendors on file.

        Call this first on every invoice, before judging anything else -- the
        vendor id it returns is required by the history and duplicate checks,
        and a vendor that fails to resolve is itself a finding.

        Args:
            vendor_name: The vendor name exactly as printed on the invoice.
        """
        out = await tool_impls.lookup_vendor(session, vendor_name=vendor_name)
        transcript.record_tool_call("lookup_vendor", {"vendor_name": vendor_name}, out)
        return json.dumps(out)

    @beta_async_tool
    async def get_invoice_history(vendor_id: str, lookback_days: int = 365) -> str:
        """Summary statistics for this vendor's past invoices.

        Call this to judge whether the current amount is normal for this
        vendor. Returns aggregates only, not individual invoices.

        Args:
            vendor_id: Vendor id returned by lookup_vendor.
            lookback_days: How far back to look. Defaults to one year.
        """
        args = {"vendor_id": vendor_id, "lookback_days": lookback_days}
        out = await tool_impls.get_invoice_history(session, **args)
        transcript.record_tool_call("get_invoice_history", args, out)
        return json.dumps(out)

    @beta_async_tool
    async def check_duplicate_invoice(vendor_id: str, amount: float,
                                      invoice_number: str | None = None) -> str:
        """Check whether this invoice has already been paid.

        Call this on every invoice before approving. Duplicate submission is a
        common vendor error and a known fraud pattern.

        Args:
            vendor_id: Vendor id returned by lookup_vendor.
            amount: The invoice total.
            invoice_number: The invoice number, if the invoice has one.
        """
        args = {"vendor_id": vendor_id, "amount": amount, "invoice_number": invoice_number}
        out = await tool_impls.check_duplicate_invoice(session, **args)
        transcript.record_tool_call("check_duplicate_invoice", args, out)
        return json.dumps(out)

    @beta_async_tool
    async def get_purchase_order(po_number: str) -> str:
        """Look up a purchase order by number.

        Call this whenever the invoice references a PO, to check the PO exists
        and compare its amount against the invoice total.

        Args:
            po_number: The purchase order number referenced on the invoice.
        """
        out = await tool_impls.get_purchase_order(session, po_number=po_number)
        transcript.record_tool_call("get_purchase_order", {"po_number": po_number}, out)
        return json.dumps(out)

    @beta_async_tool
    async def submit_recommendation(decision: str, confidence: float, reasoning: str) -> str:
        """Submit your final decision. Call this exactly once, last.

        Args:
            decision: One of "approve", "reject", or "escalate".
            confidence: Your confidence from 0.0 to 1.0.
            reasoning: Why you reached this decision, citing what the tools returned.
        """
        args = {"decision": decision, "confidence": confidence, "reasoning": reasoning}
        out = tool_impls.submit_recommendation(**args)
        transcript.record_tool_call("submit_recommendation", args, out)
        transcript.record_final(
            decision=out["final_decision"],
            confidence=out["confidence"],
            reasoning=out["reasoning"],
        )
        return json.dumps(out)

    return [
        lookup_vendor,
        get_invoice_history,
        check_duplicate_invoice,
        get_purchase_order,
        # PHASE 5 appends search_policy here -- leave it out for the baseline.
        submit_recommendation,
    ]


def describe_invoice(invoice: Invoice) -> str:
    """Render the invoice for the opening prompt.

    Explicit field selection, not `invoice.__dict__` -- that would leak
    SQLAlchemy's internal `_sa_instance_state` into the prompt and silently
    change what the model sees whenever the schema changes.
    """
    return json.dumps({
        "invoice_id": str(invoice.id),
        "vendor_name_as_printed": invoice.raw_text,
        "invoice_number": invoice.invoice_number,
        "amount": float(invoice.amount) if invoice.amount is not None else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "po_number": invoice.po_number,
    }, indent=2)


async def run_agent(session, invoice, source: str = "live") -> RunTranscript:
    transcript = RunTranscript(invoice_id=invoice.id, source=source)

    runner = client.beta.messages.tool_runner(
        model="claude-opus-5",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=build_tools(session, transcript, invoice),
        messages=[{"role": "user", "content": f"Review this invoice:\n{describe_invoice(invoice)}"}],
        max_iterations=MAX_ITERATIONS,
    )

    async for _message in runner:
        # submit_recommendation records the decision as a side effect, so the
        # transcript is the signal that the agent is done. Stopping here saves
        # the wrap-up turn the model would otherwise take after its last tool.
        if transcript.decision is not None:
            break

    if transcript.decision is None:
        # Ran out of iterations without committing to a decision. Escalate --
        # never silently approve because the agent went quiet.
        transcript.record_final(
            decision="escalate",
            confidence=0.0,
            reasoning="Agent did not submit a recommendation within the iteration limit.",
        )

    await transcript.save(session)
    return transcript
```

Three things worth noticing in that file, because they're the parts that differ from a hand-rolled loop:

- **`max_tokens=16000`, not 2048.** On Claude Opus 5 thinking is on by default, and `max_tokens` caps thinking *plus* response text together. A budget sized for the visible answer alone will truncate mid-decision.
- **The transcript is the completion signal**, not a `stop_reason` check. `submit_recommendation` records the decision as a side effect of running, so `transcript.decision is not None` means the agent has committed.
- **Every exit path lands on a decision.** If the iteration cap is hit with nothing submitted, the agent is escalated rather than left in limbo — the same "never silently approve" principle as the confidence threshold.

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/agent/prompts.py backend/app/agent/runner.py backend/tests/test_runner.py
git commit -m "feat: wire agent tools into the SDK tool runner"
```

---

### Task 23: Run the agent live on a real invoice (manual checkpoint, no new test)

**Files:** none created — this is a manual verification step before moving to the eval harness.

**Step 1: Write a one-off script**

```python
# backend/scratch_run.py — not committed, or committed under fixtures/ and deleted after
import asyncio
from app.db import SessionLocal
from app.models import Invoice
from app.extraction.pipeline import extract_invoice
from app.agent.loop import run_agent

async def main():
    result = extract_invoice("fixtures/invoices/clean_acme.pdf")
    async with SessionLocal() as session:
        invoice = Invoice(
            raw_pdf_path="fixtures/invoices/clean_acme.pdf",
            raw_text=result.raw_text,
            amount=result.fields.amount,
            po_number=result.fields.po_number,
            status="pending",
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)

        transcript = await run_agent(session, invoice)
        print("Decision:", transcript.decision, "Confidence:", transcript.confidence)
        print("Reasoning:", transcript.reasoning)
        for call in transcript.tool_calls:
            print(" -", call["tool"], call["input"], "->", call["output"])

asyncio.run(main())
```

**Step 2: Run it**

```bash
cd backend && python scratch_run.py
```

**Step 3: Read the output.** Confirm the agent actually called `lookup_vendor` and other relevant tools, and that its final decision is defensible given what those tools returned. This is Step 6 from the evals framework ("check the transcripts") applied before you've even written the eval harness — catching a broken tool or a confused agent now is cheaper than debugging it through a failing eval case later.

**Step 4: Delete the scratch script** (or move it to `backend/scripts/` if you want to keep it as a debugging utility — your call, not committed either way as part of this task).

---

### Task 24: Eval harness — fixture format + clean-state runner

**Files:**
- Create: `backend/app/eval/__init__.py`
- Create: `backend/app/eval/cases.py`
- Create: `backend/app/eval/harness.py`
- Test: `backend/tests/test_harness.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_harness.py
import pytest
from app.eval.cases import EvalCase
from app.eval.harness import run_case

@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_case_seeds_state_runs_agent_and_rolls_back(db_session):
    case = EvalCase(
        name="clean_pass_through",
        vendor={"name": "ACME Incorporated", "normalized_name": "acme incorporated", "bank_details": "IBAN1"},
        invoice={"amount": 500.0, "po_number": None, "raw_text": "ACME Incorporated invoice for $500, no PO."},
        expected_decision="approve",
        expected_tools=["lookup_vendor"],
    )
    result = await run_case(db_session, case, trials=1)
    assert result.trials[0].decision in {"approve", "reject", "escalate"}
    # the seeded vendor should not leak to the next test — verified by conftest's rollback fixture
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_harness.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/eval/cases.py`**

```python
from dataclasses import dataclass, field

@dataclass
class EvalCase:
    name: str
    vendor: dict | None
    invoice: dict
    expected_decision: str
    expected_tools: list[str] = field(default_factory=list)
    past_invoices: list[dict] = field(default_factory=list)
    purchase_order: dict | None = None
```

**Step 4: Write `backend/app/eval/harness.py`**

```python
from dataclasses import dataclass
from app.models import Vendor, Invoice, PurchaseOrder
from app.agent.loop import run_agent
from app.eval.cases import EvalCase

@dataclass
class TrialResult:
    decision: str
    confidence: float
    tools_called: list[str]

@dataclass
class CaseResult:
    case_name: str
    trials: list[TrialResult]

async def _seed(session, case: EvalCase) -> Invoice:
    vendor = None
    if case.vendor:
        vendor = Vendor(**case.vendor)
        session.add(vendor)
        await session.flush()

    if case.purchase_order:
        session.add(PurchaseOrder(**case.purchase_order))

    for past in case.past_invoices:
        session.add(Invoice(vendor_id=vendor.id if vendor else None, status="approved", raw_pdf_path="x.pdf", **past))

    invoice = Invoice(
        vendor_id=vendor.id if vendor else None,
        status="pending",
        raw_pdf_path="eval_fixture.pdf",
        **case.invoice,
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice

async def run_case(session, case: EvalCase, trials: int = 3) -> CaseResult:
    results = []
    for _ in range(trials):
        invoice = await _seed(session, case)
        transcript = await run_agent(session, invoice, source="eval")
        results.append(TrialResult(
            decision=transcript.decision,
            confidence=transcript.confidence,
            tools_called=[c["tool"] for c in transcript.tool_calls],
        ))
        await session.rollback()  # clean state for the next trial
    return CaseResult(case_name=case.name, trials=results)
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_harness.py -v -m integration`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/eval/ backend/tests/test_harness.py
git commit -m "feat: add eval harness with per-trial clean-state seeding"
```

---

### Task 25: Code-based graders

**Files:**
- Create: `backend/app/eval/graders.py`
- Test: `backend/tests/test_graders.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_graders.py
from app.eval.cases import EvalCase
from app.eval.harness import TrialResult
from app.eval.graders import grade_outcome, grade_tool_calls

def test_grade_outcome_pass():
    case = EvalCase(name="x", vendor=None, invoice={}, expected_decision="approve")
    trial = TrialResult(decision="approve", confidence=0.9, tools_called=[])
    assert grade_outcome(case, trial) is True

def test_grade_outcome_fail():
    case = EvalCase(name="x", vendor=None, invoice={}, expected_decision="approve")
    trial = TrialResult(decision="reject", confidence=0.9, tools_called=[])
    assert grade_outcome(case, trial) is False

def test_grade_tool_calls_pass_when_expected_tools_present():
    case = EvalCase(name="x", vendor=None, invoice={}, expected_decision="approve", expected_tools=["lookup_vendor"])
    trial = TrialResult(decision="approve", confidence=0.9, tools_called=["lookup_vendor", "submit_recommendation"])
    assert grade_tool_calls(case, trial) is True

def test_grade_tool_calls_fail_when_expected_tool_missing():
    case = EvalCase(name="x", vendor=None, invoice={}, expected_decision="approve", expected_tools=["get_purchase_order"])
    trial = TrialResult(decision="approve", confidence=0.9, tools_called=["lookup_vendor"])
    assert grade_tool_calls(case, trial) is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_graders.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/eval/graders.py`**

```python
from app.eval.cases import EvalCase
from app.eval.harness import TrialResult

def grade_outcome(case: EvalCase, trial: TrialResult) -> bool:
    return trial.decision == case.expected_decision

def grade_tool_calls(case: EvalCase, trial: TrialResult) -> bool:
    return all(tool in trial.tools_called for tool in case.expected_tools)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_graders.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/eval/graders.py backend/tests/test_graders.py
git commit -m "feat: add code-based outcome and tool-call graders"
```

---

### Task 26: LLM groundedness grader

**Files:**
- Modify: `backend/app/eval/graders.py`
- Modify: `backend/tests/test_graders.py`

**Step 1: Write the failing test**

```python
import pytest
from app.eval.graders import grade_groundedness

@pytest.mark.integration
def test_grade_groundedness_flags_hallucinated_reasoning():
    tool_calls = [{"tool": "lookup_vendor", "input": {"vendor_name": "Acme"}, "output": {"matched": True, "vendor_id": "abc"}}]
    reasoning_grounded = "The vendor matched an existing record, so I approved it."
    reasoning_hallucinated = "The vendor has been a customer for 15 years and always pays on time."

    assert grade_groundedness(tool_calls, reasoning_grounded)["grounded"] is True
    assert grade_groundedness(tool_calls, reasoning_hallucinated)["grounded"] is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_graders.py -v -m integration -k groundedness`
Expected: FAIL — not defined

**Step 3: Append to `backend/app/eval/graders.py`**

```python
from anthropic import Anthropic
from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)

GROUNDEDNESS_TOOL = {
    "name": "record_groundedness_judgment",
    "description": "Record whether the reasoning is grounded in the tool call results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "grounded": {"type": "boolean"},
            "explanation": {"type": "string"},
        },
        "required": ["grounded", "explanation"],
    },
}

def grade_groundedness(tool_calls: list[dict], reasoning: str) -> dict:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=512,
        tools=[GROUNDEDNESS_TOOL],
        tool_choice={"type": "tool", "name": "record_groundedness_judgment"},
        messages=[{
            "role": "user",
            "content": (
                "Here are the tool calls an agent made, and the reasoning it gave for its final decision. "
                "Judge whether every claim in the reasoning is actually supported by the tool call outputs, "
                "or whether it states something the tools never returned (hallucinated).\n\n"
                f"Tool calls: {tool_calls}\n\nReasoning: {reasoning}"
            ),
        }],
    )
    tool_call = next(b for b in response.content if b.type == "tool_use")
    return tool_call.input
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_graders.py -v -m integration -k groundedness`
Expected: PASS. If it's flaky, that's the calibration step from the evals framework (Step 6/Step 5 — "calibrate LLM judges against human experts") — read a handful of real outputs and tighten the prompt if the judge disagrees with your own read.

**Step 5: Commit**

```bash
git add backend/app/eval/graders.py backend/tests/test_graders.py
git commit -m "feat: add LLM-rubric groundedness grader for agent reasoning"
```

---

### Task 27: Write the 12 eval cases

**Files:**
- Create: `backend/app/eval/suite.py`
- Create: `backend/app/eval/report.py`

**Step 1: Write the case definitions**

Each case seeds exactly what it needs to test one thing. Two rules govern this suite:

**Every case must test a rule the policy actually states.** The governing clause for most is §II: *"discrepancies between the vendor invoice and the purchase order greater than 10 percent or $1,000 USD or equivalent in local currency (the lesser of the two) must be resolved before the payment can be processed."* A case asserting a rule the corpus doesn't contain is a broken task, not an agent failure — that distinction is Step 7 of `AI-Agent-Evals.md` and it's the most common way eval suites go wrong.

**Every anomaly case is paired with a near-identical case that should pass.** Cases 6/7/8 vary only in how far the invoice diverges from its PO; 2 and 3 differ only in whether the invoice number matches exactly. Without the pairs, an agent that escalates everything scores well — the one-sided-optimization trap from Step 3 of the evals doc.

Note which tools each case expects. Cases 6-9 and 11 expect `search_policy_tool`, which **does not exist during the Phase 4 baseline run** — those are the cases expected to fail before Phase 5, and the whole reason for running the suite twice.

```python
# backend/app/eval/suite.py
from app.eval.cases import EvalCase

ACME = {"name": "ACME Incorporated", "normalized_name": "acme incorporated", "bank_details": "IBAN GB00ACME00000000000001"}

CASES = [
    # --- Cases 1-5, 10, 12: answerable from structured tools alone.
    # --- These should pass at the Phase 4 baseline, before RAG exists.
    EvalCase(
        name="01_clean_approve",
        vendor=ACME,
        invoice={"amount": 5000.0, "po_number": "PO-1", "raw_text": "ACME Incorporated invoice, $5,000, PO-1."},
        purchase_order={"po_number": "PO-1", "amount": 5000.0},
        expected_decision="approve",
        expected_tools=["lookup_vendor", "get_purchase_order"],
    ),
    EvalCase(
        name="02_exact_duplicate_reject",
        vendor=ACME,
        invoice={"amount": 500.0, "invoice_number": "INV-1", "raw_text": "ACME Incorporated invoice INV-1, $500."},
        past_invoices=[{"amount": 500.0, "invoice_number": "INV-1"}],
        expected_decision="reject",
        expected_tools=["check_duplicate_invoice"],
    ),
    EvalCase(
        # pairs with 02 -- near-duplicate, not exact, so escalate rather than reject
        name="03_near_duplicate_escalate",
        vendor=ACME,
        invoice={"amount": 500.0, "invoice_number": "INV-1-A", "raw_text": "ACME Incorporated invoice INV-1-A, $500."},
        past_invoices=[{"amount": 500.0, "invoice_number": "INV-1"}],
        expected_decision="escalate",
        expected_tools=["check_duplicate_invoice"],
    ),
    EvalCase(
        name="04_vendor_name_drift_approve",
        vendor=ACME,
        invoice={"amount": 500.0, "raw_text": "Acme Inc invoice, $500, no PO."},  # drifted name in the text
        expected_decision="approve",
        expected_tools=["lookup_vendor"],
    ),
    EvalCase(
        # §III.A Step 1: vendor must be correctly set up before payment
        name="05_vendor_not_on_file_escalate",
        vendor=None,
        invoice={"amount": 500.0, "raw_text": "Nonesuch Trading LLC invoice, $500, no PO."},
        expected_decision="escalate",
        expected_tools=["lookup_vendor"],
    ),

    # --- Cases 6-9, 11: depend on rules that exist ONLY in the policy.
    # --- Expected to fail at the Phase 4 baseline; that gap is the measurement.
    EvalCase(
        # 6% and $400 -- inside BOTH the percentage and the dollar cap
        name="06_po_variance_within_tolerance_approve",
        vendor=ACME,
        invoice={"amount": 6400.0, "po_number": "PO-2", "raw_text": "ACME Incorporated invoice, $6,400, PO-2."},
        purchase_order={"po_number": "PO-2", "amount": 6000.0},
        expected_decision="approve",
        expected_tools=["get_purchase_order", "search_policy_tool"],
    ),
    EvalCase(
        # pairs with 06 -- 15% and $3,000, outside both limits
        name="07_po_variance_outside_tolerance_escalate",
        vendor=ACME,
        invoice={"amount": 23000.0, "po_number": "PO-3", "raw_text": "ACME Incorporated invoice, $23,000, PO-3."},
        purchase_order={"po_number": "PO-3", "amount": 20000.0},
        expected_decision="escalate",
        expected_tools=["get_purchase_order", "search_policy_tool"],
    ),
    EvalCase(
        # 4% but $2,500 absolute: inside the percentage, OUTSIDE the dollar cap.
        # "the lesser of the two" governs -- catches an agent that skims "10 percent".
        name="08_po_variance_lesser_of_two_escalate",
        vendor=ACME,
        invoice={"amount": 65000.0, "po_number": "PO-4", "raw_text": "ACME Incorporated invoice, $65,000, PO-4."},
        purchase_order={"po_number": "PO-4", "amount": 62500.0},
        expected_decision="escalate",
        expected_tools=["get_purchase_order", "search_policy_tool"],
    ),
    EvalCase(
        # The policy defers the PO-required threshold to the Procurement Procedures,
        # a document NOT in the corpus. Correct behavior is to escalate rather than
        # invent a number -- tests whether the agent knows the limits of what it read.
        name="09_large_invoice_no_po_escalate",
        vendor=ACME,
        invoice={"amount": 40000.0, "raw_text": "ACME Incorporated invoice, $40,000, no purchase order referenced."},
        expected_decision="escalate",
        expected_tools=["search_policy_tool"],
    ),
    EvalCase(
        name="10_amount_outlier_escalate",
        vendor=ACME,
        invoice={"amount": 25000.0, "raw_text": "ACME Incorporated invoice, $25,000, no PO."},
        past_invoices=[{"amount": 900.0}, {"amount": 1000.0}, {"amount": 1100.0}],
        expected_decision="escalate",
        expected_tools=["get_invoice_history"],
    ),
    EvalCase(
        # §IV.F Currency of payments -- a valid non-USD invoice should still approve,
        # with the currency handling cited rather than treated as an anomaly.
        name="11_non_usd_currency_approve",
        vendor=ACME,
        invoice={"amount": 4500.0, "po_number": "PO-5",
                 "raw_text": "ACME Incorporated invoice, EUR 4,500.00, PO-5. Payment in local currency."},
        purchase_order={"po_number": "PO-5", "amount": 4500.0},
        expected_decision="approve",
        expected_tools=["search_policy_tool"],
    ),
    EvalCase(
        name="12_low_quality_scan_forced_escalate",
        vendor=None,
        invoice={"amount": None, "raw_text": "???ACME??? invoi... $5??.00 ... due ??/??/2026"},
        expected_decision="escalate",
        expected_tools=[],
    ),
]
```

**Step 2: Wire a runner script**

The runner writes results to a JSON file as well as printing them, so the Phase 4 baseline can be compared against the Phase 5 re-run rather than remembered.

```python
# backend/app/eval/report.py
import asyncio
import json
import sys
from app.db import SessionLocal
from app.eval.suite import CASES
from app.eval.harness import run_case
from app.eval.graders import grade_outcome, grade_tool_calls

async def run_all(label: str):
    results = {}
    async with SessionLocal() as session:
        for case in CASES:
            result = await run_case(session, case, trials=3)
            outcome_passes = [grade_outcome(case, t) for t in result.trials]
            tool_passes = [grade_tool_calls(case, t) for t in result.trials]
            results[case.name] = {
                "pass_at_1": outcome_passes[0],
                "pass_hat_k": all(outcome_passes),
                "tool_calls_ok": all(tool_passes),
                "trials": [
                    {"decision": t.decision, "confidence": t.confidence, "tools_called": t.tools_called}
                    for t in result.trials
                ],
            }
            r = results[case.name]
            print(f"{case.name}: pass@1={r['pass_at_1']} pass^3={r['pass_hat_k']} tools_ok={r['tool_calls_ok']}")
            for i, trial in enumerate(result.trials):
                print(f"    trial {i}: decision={trial.decision} confidence={trial.confidence} tools={trial.tools_called}")

    passed = sum(1 for r in results.values() if r["pass_at_1"])
    print(f"\n{label}: {passed}/{len(CASES)} pass@1")

    path = f"eval_results_{label}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"wrote {path}")

if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    asyncio.run(run_all(label))
```

**Step 3: Run the baseline and read every transcript**

```bash
cd backend && python -m app.eval.report baseline
```

This is the Phase 4 baseline. Two things to do with it:

1. **Read the transcripts, don't just read the score.** Per Step 6 of `AI-Agent-Evals.md`, a failure should look *fair* — it should be obvious what the agent got wrong and why. If a case fails because the seed data doesn't contain what the grader checks for, that's a broken task, not an incapable agent (Step 7). Fix those before treating the number as meaningful.
2. **Expect cases 6-9 and 11 to fail**, and confirm they fail *for the predicted reason* — the agent guessing at a tolerance or threshold it has no way to look up. If one of them passes, read why: the agent may have guessed a plausible number and gotten lucky, which is worth knowing, or the case may not actually require the policy.

Commit the baseline JSON — it's the thing Phase 5 is measured against.

**Step 4: Commit**

```bash
git add backend/app/eval/suite.py backend/app/eval/report.py backend/eval_results_baseline.json
git commit -m "feat: add 12 paired eval cases, pass@1/pass^k reporting, and baseline results"
```

---

### Task 28: FastAPI app + `POST /eval/run` endpoint

**Files:**
- Create: `backend/app/main.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_main.py`
- Test: `backend/tests/test_eval_endpoint.py`

This is the first time FastAPI shows up in the project — everything up to now has been runnable as plain scripts. It's introduced now because the eval harness (and later, the dashboard) is the first thing that actually needs an HTTP interface.

**Step 1: Extend `backend/requirements.txt`**

```
fastapi
uvicorn[standard]
httpx
```

**Step 2: Write the failing tests**

```python
# backend/tests/test_main.py
from fastapi.testclient import TestClient
from app.main import app

def test_health_check():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

```python
# backend/tests/test_eval_endpoint.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.integration
@pytest.mark.asyncio
async def test_eval_run_endpoint_returns_case_results():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/eval/run")
    assert response.status_code == 200
    body = response.json()
    assert len(body["cases"]) == 12
    assert "pass_at_1" in body["cases"][0]
```

**Step 3: Run tests to verify they fail**

Run: `pytest tests/test_main.py tests/test_eval_endpoint.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 4: Write `backend/app/main.py`**

```python
from fastapi import FastAPI
from app.db import SessionLocal
from app.eval.suite import CASES
from app.eval.harness import run_case
from app.eval.graders import grade_outcome, grade_tool_calls

app = FastAPI(title="Invoice Agent")

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/eval/run")
async def eval_run():
    results = []
    async with SessionLocal() as session:
        for case in CASES:
            case_result = await run_case(session, case, trials=3)
            outcome_passes = [grade_outcome(case, t) for t in case_result.trials]
            tool_passes = [grade_tool_calls(case, t) for t in case_result.trials]
            results.append({
                "name": case.name,
                "pass_at_1": outcome_passes[0],
                "pass_hat_k": all(outcome_passes),
                "tool_calls_ok": all(tool_passes),
                "trials": [{"decision": t.decision, "confidence": t.confidence, "tools_called": t.tools_called} for t in case_result.trials],
            })
    return {"cases": results}
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_main.py tests/test_eval_endpoint.py -v -m integration`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/main.py backend/requirements.txt backend/tests/test_main.py backend/tests/test_eval_endpoint.py
git commit -m "feat: add FastAPI app with health check and POST /eval/run"
```

---

### Task 29: Wire `search_policy` into the loop and re-measure — **PHASE 5**

The last task. Everything in Phase 5 up to here built the retrieval machinery; this connects it and produces the comparison the resequencing exists for.

**Files:**
- Modify: `backend/app/agent/runner.py`
- Modify: `backend/app/agent/prompts.py`

**Step 1: Add one decorated tool inside `build_tools`**, at the marked spot:

```python
    @beta_async_tool
    async def search_policy(query: str) -> str:
        """Search the written AP policy for the clauses governing this invoice.

        Call this whenever your decision depends on a rule -- an approval
        threshold, a tolerance band, a required control -- rather than asserting
        the rule from memory. Returns policy sections with their headings so you
        can cite them.

        Args:
            query: What you need the policy to tell you, in plain language.
        """
        out = await tool_impls.search_policy_tool(session, query=query)
        transcript.record_tool_call("search_policy", {"query": query}, out)
        return json.dumps(out)
```

**Step 2: Add `search_policy` to the returned list**, at the marked spot (before `submit_recommendation`).

**Step 3: Append the policy sentence to `SYSTEM_PROMPT`** (the text given in Task 22).

That's the whole change — no schema to hand-write, no dispatch branch to add. The tool runner picks up the new tool from the list, and its docstring becomes the description Claude reads.

Change nothing else. The eval cases, graders, harness, and the other five tools stay exactly as they were — otherwise the comparison measures several changes at once and tells you nothing about retrieval specifically.

**Step 4: Load the policy corpus**

```bash
python -c "
import asyncio, pdfplumber
from app.db import SessionLocal
from app.rag.chunking import chunk_policy_text
from app.rag.store import store_policy_chunks

async def main():
    with pdfplumber.open('fixtures/policy/FINA_Accounts_Payable.pdf') as pdf:
        text = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    async with SessionLocal() as session:
        chunks = chunk_policy_text(text)
        await store_policy_chunks(session, chunks)
        print(f'loaded {len(chunks)} policy chunks')

asyncio.run(main())
"
```

**Step 5: Re-run the identical suite**

```bash
python -m app.eval.report with_rag
```

**Step 6: Compare**

```bash
python -c "
import json
base = json.load(open('eval_results_baseline.json'))
rag = json.load(open('eval_results_with_rag.json'))
print(f'{\"case\":<45} {\"baseline\":>9} {\"with_rag\":>9}')
for name in base:
    b, r = base[name]['pass_at_1'], rag[name]['pass_at_1']
    flag = '  <-- changed' if b != r else ''
    print(f'{name:<45} {str(b):>9} {str(r):>9}{flag}')
print()
print('baseline:', sum(1 for v in base.values() if v['pass_at_1']), '/', len(base))
print('with rag:', sum(1 for v in rag.values() if v['pass_at_1']), '/', len(rag))
"
```

**Step 7: Read the transcripts for the cases that changed.** The number alone isn't the finding — the finding is *why*. For each newly-passing case, confirm the agent actually retrieved the governing clause and reasoned from it, rather than passing by luck. Case 8 (the "lesser of the two" case) is the sharpest test: passing it requires the agent to have read the qualifier, not just matched on "10 percent."

Also read any case that got *worse*. Retrieval can hurt — an agent handed five policy clauses may over-apply one that doesn't govern, and start escalating things it previously approved correctly. That's a real finding about tool design, and exactly the kind of thing the paired cases (6 vs 7 vs 8) are shaped to expose.

**Step 8: Commit**

```bash
git add backend/app/agent/runner.py backend/app/agent/prompts.py backend/eval_results_with_rag.json
git commit -m "feat: wire search_policy into the agent loop; record post-RAG eval results"
```

---

## What's next

This plan stops at a working backend: extraction, a tool-using agent, an eval harness you can hit via `POST /eval/run`, and a measured before/after on what policy retrieval actually buys. The remaining pieces from `Project.MD` — `POST /invoices`, `GET /invoices`, approve/reject endpoints, and the React dashboard — get their own plan once you've run this one end-to-end and are happy with how the agent performs on the 12 cases.

## Out-of-band addition: stateless `POST /extract` + minimal frontend

Before reaching Phase 2, a `POST /extract` endpoint and a minimal Vite/React/TypeScript frontend were added on top of Phase 1 (extraction pipeline only) so there was something to look at end-to-end early. Deliberately **stateless** — it takes a PDF upload, runs `extract_invoice()`, and returns the fields as JSON. No DB, no persistence, because the `invoices`/`vendors` schema doesn't exist until Phase 3 (Task 13).

This means `backend/app/main.py` already exists by the time Task 28 comes around — Task 28 should **extend** it (add `/eval/run` alongside the existing `/health` and `/extract`), not create it from scratch. `requirements.txt` will already have `fastapi`, `uvicorn[standard]`, `python-multipart`, and `httpx` from this addition too — skip re-adding them in Task 28.

**When Phase 3 lands** (Task 13 onward adds the `Invoice`/`Vendor` tables), `POST /extract` is the natural thing to evolve into the real `POST /invoices` upload endpoint from `Project.MD` — same extraction call, but now it persists an `Invoice` row and kicks off the agent, instead of just returning JSON. Don't build a second, separate upload endpoint — extend this one.

The frontend (`frontend/`) at this point is just the upload-and-view-results page. It becomes the approval dashboard once Phase 3's agent and `POST /invoices`/approve/reject endpoints exist — same app, more views, not a rewrite.
