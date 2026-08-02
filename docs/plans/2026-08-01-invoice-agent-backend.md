# Invoice Agent Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the backend for the invoice agent — extraction pipeline, RAG, a tool-using agent, and an eval harness — so it can be driven from a script/API before any frontend exists.

**Architecture:** FastAPI app backed by Postgres (pgvector for embeddings), SQLAlchemy 2.0 async ORM + Alembic migrations. A hybrid text-layer/vision extraction pipeline feeds structured fields and RAG chunks. A hand-rolled Anthropic tool-use loop drives the agent over six tools. An eval harness runs the same agent through 9 fixture cases with per-trial DB isolation, reusing the `agent_runs` transcript table for both live and eval runs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async, `asyncpg` driver), Alembic, Postgres 16 + pgvector (Docker Compose), `anthropic` SDK, `voyageai` SDK, `pdfplumber`, pytest, pytest-asyncio.

**Frontend is out of scope for this plan** — it gets its own plan once this backend runs end-to-end (per the design in `Project.MD`).

---

## Phase 0 — Scaffolding

### Task 1: Repo skeleton + Postgres via Docker Compose

**Files:**
- Create: `backend/docker-compose.yml`
- Create: `backend/.env.example`
- Create: `backend/requirements.txt`
- Create: `backend/.gitignore`
- Create: `backend/app/__init__.py`
- Create: `backend/tests/__init__.py`

**Step 1: Create the directory structure**

```bash
mkdir -p backend/app backend/tests backend/fixtures/invoices backend/fixtures/policy
touch backend/app/__init__.py backend/tests/__init__.py
```

**Step 2: Write `backend/docker-compose.yml`**

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

**Step 3: Write `backend/.env.example`**

```
DATABASE_URL=postgresql+asyncpg://invoice_agent:invoice_agent@localhost:5432/invoice_agent
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
CONFIDENCE_ESCALATION_THRESHOLD=0.7
```

Copy it: `cp backend/.env.example backend/.env` and fill in real API keys.

**Step 4: Write `backend/requirements.txt`**

```
fastapi
uvicorn[standard]
sqlalchemy>=2.0
asyncpg
alembic
anthropic
voyageai
pdfplumber
pydantic-settings
pytest
pytest-asyncio
httpx
```

**Step 5: Write `backend/.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
venv/
```

**Step 6: Set up the virtualenv and start Postgres**

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
docker compose up -d
```

Expected: `docker compose ps` shows the `db` service healthy on port 5432.

**Step 7: Commit**

```bash
git add backend/docker-compose.yml backend/.env.example backend/requirements.txt backend/.gitignore backend/app/__init__.py backend/tests/__init__.py
git commit -m "chore: scaffold backend project with Postgres/pgvector via Docker"
```

---

### Task 2: Config + DB engine

**Files:**
- Create: `backend/app/config.py`
- Create: `backend/app/db.py`
- Test: `backend/tests/test_config.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_config.py
import os
from app.config import Settings

def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://u:p@localhost/db"
    assert settings.confidence_escalation_threshold == 0.7
```

**Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

**Step 3: Write `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    anthropic_api_key: str
    voyage_api_key: str
    confidence_escalation_threshold: float = 0.7

    class Config:
        env_file = ".env"

settings = Settings()
```

**Step 4: Write `backend/app/db.py`**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/config.py backend/app/db.py backend/tests/test_config.py
git commit -m "feat: add settings and async DB engine"
```

---

### Task 3: SQLAlchemy models + Alembic migration

**Files:**
- Create: `backend/app/models.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_initial.py`

**Step 1: Write `backend/app/models.py`**

```python
import uuid
from datetime import datetime, date
from sqlalchemy import String, Numeric, Date, DateTime, ForeignKey, Text, Float
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

class Base(DeclarativeBase):
    pass

def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

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
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    line_items: Mapped[list["LineItem"]] = relationship(back_populates="invoice")

class LineItem(Base):
    __tablename__ = "line_items"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)

    invoice: Mapped["Invoice"] = relationship(back_populates="line_items")

class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)  # "invoice" | "policy"
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)

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

Add `pgvector` to `backend/requirements.txt` (the Python client library, distinct from the Postgres extension image already in Docker Compose):

```
pgvector
```

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
docker compose exec db psql -U invoice_agent -d invoice_agent -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

**Step 5: Generate and run the migration**

```bash
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Expected: a new file under `backend/alembic/versions/`, and `alembic upgrade head` completes without error.

**Step 6: Verify the tables exist**

```bash
docker compose exec db psql -U invoice_agent -d invoice_agent -c "\dt"
```

Expected: `vendors`, `invoices`, `line_items`, `documents`, `agent_runs`, `audit_log`.

**Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic.ini backend/alembic/ backend/requirements.txt
git commit -m "feat: add SQLAlchemy models and initial Alembic migration"
```

---

### Task 4: FastAPI app skeleton + health check

**Files:**
- Create: `backend/app/main.py`
- Test: `backend/tests/test_main.py`

**Step 1: Write the failing test**

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

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

**Step 3: Write `backend/app/main.py`**

```python
from fastapi import FastAPI

app = FastAPI(title="Invoice Agent")

@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat: add FastAPI app skeleton with health check"
```

---

## Phase 1 — Fixtures

### Task 5: Source sample invoices + seed data

**Files:**
- Create: `backend/fixtures/invoices/clean_acme.pdf`
- Create: `backend/fixtures/invoices/clean_globex.pdf`
- Create: `backend/fixtures/invoices/messy_scanned.pdf`
- Create: `backend/fixtures/policy/ap_policy.md`
- Create: `backend/fixtures/seed_vendors.py`

This task is manual sourcing, not TDD — there's no test to write first.

**Step 1: Get 2-3 clean, born-digital invoice PDFs**

Search "sample invoice PDF" or generate simple ones (a Google Doc/Word doc with invoice fields, exported to PDF works fine). You need real text you can verify extraction against — write down the ground-truth vendor name, amount, due date, and line items for each one somewhere you'll reference later (Task 26 needs this).

Name vendors so one has a naming variant you'll test later, e.g. one PDF says "Acme Inc" — you'll later seed the `vendors` table with "ACME Incorporated" so Task 14's fuzzy match has something real to resolve.

**Step 2: Get one messy/scanned invoice PDF**

Either photograph/scan a printed invoice at an angle, or find a low-quality scanned sample online. This is what exercises the vision fallback in Task 6.

**Step 3: Write a short AP policy doc**

```markdown
# backend/fixtures/policy/ap_policy.md

# AP Approval Policy

- Invoices under $10,000 may be auto-approved if extraction confidence is high and no anomalies are found.
- Invoices of $10,000 or more require documented evidence of a second approval before payment. If no such evidence is found in the invoice or attached correspondence, escalate to human review.
- Any invoice referencing a purchase order must have that PO's amount match within 5%, or escalate.
- Bank account details that differ from the vendor's records on file must never be auto-approved — always escalate for manual verification.
```

**Step 4: Write vendor seed script**

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

Run: `cd backend && python -m fixtures.seed_vendors`

**Step 5: Commit**

```bash
git add backend/fixtures/
git commit -m "chore: add sample invoice PDFs, AP policy doc, and vendor seed script"
```

---

## Phase 2 — Extraction pipeline

### Task 6: Text-layer extraction + usability heuristic

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

### Task 7: Vision fallback transcription

**Files:**
- Create: `backend/app/extraction/vision_fallback.py`
- Test: `backend/tests/test_vision_fallback.py`

**Step 1: Write the failing test (integration test, calls real API)**

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

Register the marker in `backend/pytest.ini`:

```ini
[pytest]
markers =
    integration: tests that call a real external API (costs money, needs API keys)
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_vision_fallback.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/extraction/vision_fallback.py`**

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

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_vision_fallback.py -v -m integration`
Expected: PASS (requires `ANTHROPIC_API_KEY` set in `.env`)

**Step 5: Commit**

```bash
git add backend/app/extraction/vision_fallback.py backend/tests/test_vision_fallback.py backend/pytest.ini
git commit -m "feat: add vision-based transcription fallback for scanned invoices"
```

---

### Task 8: Structured field extraction

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

### Task 9: Extraction pipeline orchestrator

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

**Step 5: Commit**

```bash
git add backend/app/extraction/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: wire text-layer/vision/field-extraction into one pipeline"
```

---

## Phase 3 — RAG

### Task 10: Chunking

**Files:**
- Create: `backend/app/rag/__init__.py`
- Create: `backend/app/rag/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_chunking.py
from app.rag.chunking import chunk_invoice_text, chunk_policy_text

def test_chunk_invoice_text_fixed_size():
    text = "A" * 1200
    chunks = chunk_invoice_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 3
    assert len(chunks[0]) == 500
    # overlap: end of chunk 0 should reappear at start of chunk 1
    assert chunks[0][-50:] == chunks[1][:50]

def test_chunk_policy_text_by_paragraph():
    text = "First point.\n\nSecond point.\n\nThird point."
    chunks = chunk_policy_text(text)
    assert chunks == ["First point.", "Second point.", "Third point."]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_chunking.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/rag/chunking.py`**

```python
def chunk_invoice_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += step
    return chunks

def chunk_policy_text(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n")]
    return [p for p in paragraphs if p]
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_chunking.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/rag/__init__.py backend/app/rag/chunking.py backend/tests/test_chunking.py
git commit -m "feat: add fixed-size invoice chunking and paragraph-based policy chunking"
```

---

### Task 11: Voyage embeddings client

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

Note: `input_type="document"` when embedding chunks to store, `input_type="query"` when embedding a search query in Task 13 — Voyage tunes the embedding differently for each.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_embeddings.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/rag/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat: add Voyage AI embeddings client wrapper"
```

---

### Task 12: Store chunks in pgvector

**Files:**
- Create: `backend/app/rag/store.py`
- Test: `backend/tests/test_store.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_store.py
import pytest
from sqlalchemy import select
from app.rag.store import store_document_chunks
from app.models import Document

@pytest.mark.asyncio
async def test_store_document_chunks(db_session):
    chunks = ["chunk one text", "chunk two text"]
    await store_document_chunks(db_session, invoice_id=None, doc_type="policy", chunks=chunks)
    result = await db_session.execute(select(Document).where(Document.doc_type == "policy"))
    rows = result.scalars().all()
    assert len(rows) == 2
    assert len(rows[0].embedding) == 1024
```

Add a `db_session` fixture in `backend/tests/conftest.py` (used by this and later DB-touching tests):

```python
# backend/tests/conftest.py
import pytest_asyncio
from app.db import SessionLocal

@pytest_asyncio.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session
        await session.rollback()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_store.py -v -m integration`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/rag/store.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Document
from app.rag.embeddings import embed_texts

async def store_document_chunks(
    session: AsyncSession, invoice_id, doc_type: str, chunks: list[str]
) -> list[Document]:
    embeddings = embed_texts(chunks, input_type="document")
    documents = [
        Document(invoice_id=invoice_id, doc_type=doc_type, chunk_text=chunk, embedding=embedding)
        for chunk, embedding in zip(chunks, embeddings)
    ]
    session.add_all(documents)
    await session.commit()
    return documents
```

Mark this test `@pytest.mark.integration` too (it calls the real Voyage API) and run it the same way as Task 11.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_store.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/rag/store.py backend/tests/test_store.py backend/tests/conftest.py
git commit -m "feat: store document chunks with embeddings in pgvector"
```

---

### Task 13: Similarity search (`search_documents`)

**Files:**
- Create: `backend/app/rag/search.py`
- Test: `backend/tests/test_search.py`

**Step 1: Write the failing test**

```python
# backend/tests/test_search.py
import pytest
from app.rag.store import store_document_chunks
from app.rag.search import search_documents

@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_returns_most_relevant_chunk(db_session):
    await store_document_chunks(
        db_session, invoice_id=None, doc_type="policy",
        chunks=[
            "Invoices under $10,000 may be auto-approved.",
            "Bank details that differ from vendor records must always escalate.",
        ],
    )
    results = await search_documents(db_session, query="what happens if bank details changed", top_k=1)
    assert "bank details" in results[0].chunk_text.lower()
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

async def search_documents(
    session: AsyncSession, query: str, invoice_id=None, top_k: int = 5
) -> list[Document]:
    query_embedding = embed_texts([query], input_type="query")[0]
    stmt = select(Document)
    if invoice_id is not None:
        stmt = stmt.where((Document.invoice_id == invoice_id) | (Document.doc_type == "policy"))
    stmt = stmt.order_by(Document.embedding.cosine_distance(query_embedding)).limit(top_k)
    result = await session.execute(stmt)
    return result.scalars().all()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_search.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/rag/search.py backend/tests/test_search.py
git commit -m "feat: add pgvector similarity search over invoice and policy chunks"
```

---

## Phase 4 — Agent, tools, and eval harness

This phase is deliberately interleaved: each tool gets built, then its eval case gets written against it, per your request to do evals concurrently rather than after.

### Task 14: Agent run transcript recording

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

### Task 15: Tool — `lookup_vendor` + eval case 3 (vendor name drift)

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

### Task 16: Tool — `get_invoice_history`

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

### Task 17: Tool — `check_duplicate_invoice`

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

### Task 18: Tool — `get_purchase_order`

**Files:**
- Create: `backend/app/models.py` — add a minimal `PurchaseOrder` table (this project fakes a PO system rather than integrating a real one)
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`
- Create: `backend/alembic/versions/0002_purchase_orders.py`

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

### Task 19: Tool — `search_documents` (wraps RAG)

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/tests/test_tools.py`

**Step 1: Write the failing test**

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_documents_tool_finds_policy_chunk(db_session):
    from app.rag.store import store_document_chunks
    await store_document_chunks(db_session, invoice_id=None, doc_type="policy", chunks=["Bank details that differ from vendor records must always escalate."])
    result = await search_documents_tool(db_session, query="changed bank account", invoice_id=None)
    assert any("bank details" in r["text"].lower() for r in result["results"])
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v -k search_documents -m integration`
Expected: FAIL — not defined

**Step 3: Append to `backend/app/agent/tools.py`**

```python
from app.rag.search import search_documents

async def search_documents_tool(session: AsyncSession, query: str, invoice_id: str | None = None) -> dict:
    docs = await search_documents(session, query=query, invoice_id=invoice_id, top_k=5)
    return {"results": [{"text": d.chunk_text, "doc_type": d.doc_type} for d in docs]}
```

Named `search_documents_tool` (not `search_documents`) to avoid clashing with the RAG-layer function it wraps — the tool-use loop in Task 20 exposes this one to the agent.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools.py -v -k search_documents -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/agent/tools.py backend/tests/test_tools.py
git commit -m "feat: add search_documents agent tool wrapping RAG similarity search"
```

---

### Task 20: Tool — `submit_recommendation` + confidence escalation

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

### Task 21: Tool-use loop orchestrator

**Files:**
- Create: `backend/app/agent/prompts.py`
- Create: `backend/app/agent/loop.py`
- Test: `backend/tests/test_loop.py`

**Step 1: Write the failing test (mocks the Anthropic client so the loop's mechanics are tested deterministically, not live model behavior)**

```python
# backend/tests/test_loop.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent.loop import run_agent

class FakeToolUseBlock:
    type = "tool_use"
    def __init__(self, name, input, id="tool_1"):
        self.name, self.input, self.id = name, input, id

class FakeTextBlock:
    type = "text"
    def __init__(self, text):
        self.text = text

@pytest.mark.asyncio
async def test_loop_executes_tool_then_stops_on_submit_recommendation(db_session, seeded_invoice, monkeypatch):
    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        response = MagicMock()
        if call_count["n"] == 1:
            response.content = [FakeToolUseBlock("lookup_vendor", {"vendor_name": "Acme"})]
            response.stop_reason = "tool_use"
        else:
            response.content = [FakeToolUseBlock(
                "submit_recommendation",
                {"decision": "approve", "confidence": 0.9, "reasoning": "vendor matched"},
            )]
            response.stop_reason = "tool_use"
        return response

    import app.agent.loop as loop_module
    monkeypatch.setattr(loop_module.client.messages, "create", fake_create)

    transcript = await run_agent(db_session, invoice=seeded_invoice)
    assert transcript.decision == "approve"
    assert len(transcript.tool_calls) == 2  # lookup_vendor, then submit_recommendation
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_loop.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Write `backend/app/agent/prompts.py`**

```python
SYSTEM_PROMPT = """You are an accounts-payable review agent. Given an invoice's extracted fields, \
investigate it using the tools available before making a recommendation. Always check the vendor \
resolves correctly, check invoice history for anomalies, check for duplicates, and check any \
referenced purchase order. Use search_documents_tool if you need to check AP policy or the \
invoice's original wording. When you have enough information, call submit_recommendation with \
your decision (approve, reject, or escalate), a confidence score from 0 to 1, and your reasoning."""
```

**Step 4: Write `backend/app/agent/loop.py`**

```python
from anthropic import Anthropic
from app.config import settings
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.transcript import RunTranscript
from app.agent import tools as tool_impls

client = Anthropic(api_key=settings.anthropic_api_key)

TOOL_DEFINITIONS = [
    {"name": "lookup_vendor", "description": "Resolve a vendor name against records on file.",
     "input_schema": {"type": "object", "properties": {"vendor_name": {"type": "string"}}, "required": ["vendor_name"]}},
    {"name": "get_invoice_history", "description": "Get summary stats of past invoices for a vendor.",
     "input_schema": {"type": "object", "properties": {"vendor_id": {"type": "string"}, "lookback_days": {"type": "integer"}}, "required": ["vendor_id"]}},
    {"name": "check_duplicate_invoice", "description": "Check whether this invoice has already been paid.",
     "input_schema": {"type": "object", "properties": {"vendor_id": {"type": "string"}, "amount": {"type": "number"}, "invoice_number": {"type": ["string", "null"]}}, "required": ["vendor_id", "amount"]}},
    {"name": "get_purchase_order", "description": "Look up a purchase order by number.",
     "input_schema": {"type": "object", "properties": {"po_number": {"type": "string"}}, "required": ["po_number"]}},
    {"name": "search_documents_tool", "description": "Semantic search over this invoice's original text and AP policy.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "invoice_id": {"type": ["string", "null"]}}, "required": ["query"]}},
    {"name": "submit_recommendation", "description": "Submit your final decision. Call this last.",
     "input_schema": {"type": "object", "properties": {
         "decision": {"type": "string", "enum": ["approve", "reject", "escalate"]},
         "confidence": {"type": "number"},
         "reasoning": {"type": "string"},
     }, "required": ["decision", "confidence", "reasoning"]}},
]

MAX_TURNS = 8

async def _dispatch(session, name: str, input: dict):
    if name == "lookup_vendor":
        return await tool_impls.lookup_vendor(session, **input)
    if name == "get_invoice_history":
        return await tool_impls.get_invoice_history(session, **input)
    if name == "check_duplicate_invoice":
        return await tool_impls.check_duplicate_invoice(session, **input)
    if name == "get_purchase_order":
        return await tool_impls.get_purchase_order(session, **input)
    if name == "search_documents_tool":
        return await tool_impls.search_documents_tool(session, **input)
    if name == "submit_recommendation":
        return tool_impls.submit_recommendation(**input)
    raise ValueError(f"Unknown tool: {name}")

async def run_agent(session, invoice, source: str = "live") -> RunTranscript:
    transcript = RunTranscript(invoice_id=invoice.id, source=source)
    messages = [{"role": "user", "content": f"Review this invoice: {invoice.__dict__}"}]

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in tool_use_blocks:
            output = await _dispatch(session, block.name, block.input)
            transcript.record_tool_call(block.name, block.input, output)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})

            if block.name == "submit_recommendation":
                transcript.record_final(
                    decision=output["final_decision"],
                    confidence=output["confidence"],
                    reasoning=output["reasoning"],
                )
                await transcript.save(session)
                return transcript

        messages.append({"role": "user", "content": tool_results})

    # hit MAX_TURNS without a submitted recommendation — force escalation
    transcript.record_final(decision="escalate", confidence=0.0, reasoning="Agent did not submit a recommendation within the turn limit.")
    await transcript.save(session)
    return transcript
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/test_loop.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add backend/app/agent/prompts.py backend/app/agent/loop.py backend/tests/test_loop.py
git commit -m "feat: add tool-use loop orchestrator wiring all agent tools together"
```

---

### Task 22: Run the agent live on a real invoice (manual checkpoint, no new test)

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

### Task 23: Eval harness — fixture format + clean-state runner

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

### Task 24: Code-based graders

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

### Task 25: LLM groundedness grader

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

### Task 26: Write the 9 eval cases

**Files:**
- Create: `backend/app/eval/suite.py`

**Step 1: Write the case definitions**

Use the ground-truth data you recorded in Task 5 for your real fixture invoices where relevant, and synthetic data for the rest. Each case seeds exactly what it needs to test one thing.

```python
# backend/app/eval/suite.py
from app.eval.cases import EvalCase

ACME = {"name": "ACME Incorporated", "normalized_name": "acme incorporated", "bank_details": "IBAN GB00ACME00000000000001"}

CASES = [
    EvalCase(
        name="01_clean_approve",
        vendor=ACME,
        invoice={"amount": 500.0, "raw_text": "ACME Incorporated invoice, $500, no PO."},
        expected_decision="approve",
        expected_tools=["lookup_vendor"],
    ),
    EvalCase(
        name="02_duplicate_reject",
        vendor=ACME,
        invoice={"amount": 500.0, "invoice_number": "INV-1", "raw_text": "ACME Incorporated invoice INV-1, $500."},
        past_invoices=[{"amount": 500.0, "invoice_number": "INV-1"}],
        expected_decision="reject",
        expected_tools=["check_duplicate_invoice"],
    ),
    EvalCase(
        name="03_vendor_name_drift_approve",
        vendor=ACME,
        invoice={"amount": 500.0, "raw_text": "Acme Inc invoice, $500, no PO."},  # note: drifted name in the text
        expected_decision="approve",
        expected_tools=["lookup_vendor"],
    ),
    EvalCase(
        name="04_amount_outlier_escalate",
        vendor=ACME,
        invoice={"amount": 25000.0, "raw_text": "ACME Incorporated invoice, $25,000, no PO."},
        past_invoices=[{"amount": 900.0}, {"amount": 1000.0}, {"amount": 1100.0}],
        expected_decision="escalate",
        expected_tools=["get_invoice_history"],
    ),
    EvalCase(
        name="05_po_mismatch_escalate",
        vendor=ACME,
        invoice={"amount": 5000.0, "po_number": "PO-1", "raw_text": "ACME Incorporated invoice, $5,000, PO-1."},
        purchase_order={"po_number": "PO-1", "amount": 2000.0},
        expected_decision="escalate",
        expected_tools=["get_purchase_order"],
    ),
    EvalCase(
        name="06_bank_details_changed_escalate",
        vendor=ACME,
        invoice={"amount": 500.0, "raw_text": "ACME Incorporated invoice, $500. New bank account: IBAN GB99DIFFERENT."},
        expected_decision="escalate",
        expected_tools=["lookup_vendor"],
    ),
    EvalCase(
        name="07_large_invoice_no_second_approval_escalate",
        vendor=ACME,
        invoice={"amount": 15000.0, "raw_text": "ACME Incorporated invoice, $15,000, no PO, no approval notes."},
        expected_decision="escalate",
        expected_tools=["search_documents_tool"],
    ),
    EvalCase(
        name="08_large_invoice_with_second_approval_approve",
        vendor=ACME,
        invoice={"amount": 15000.0, "raw_text": "ACME Incorporated invoice, $15,000. Second approval on file: J. Rivera, 2026-07-20."},
        expected_decision="approve",
        expected_tools=["search_documents_tool"],
    ),
    EvalCase(
        name="09_low_quality_scan_forced_escalate",
        vendor=None,
        invoice={"amount": None, "raw_text": "???ACME??? invoi... $5??.00 ... due ??/??/2026"},
        expected_decision="escalate",
        expected_tools=[],
    ),
]
```

**Step 2: Wire a runner script**

```python
# backend/app/eval/report.py
import asyncio
from app.db import SessionLocal
from app.eval.suite import CASES
from app.eval.harness import run_case
from app.eval.graders import grade_outcome, grade_tool_calls, grade_groundedness

async def run_all():
    async with SessionLocal() as session:
        for case in CASES:
            result = await run_case(session, case, trials=3)
            outcome_passes = [grade_outcome(case, t) for t in result.trials]
            tool_passes = [grade_tool_calls(case, t) for t in result.trials]
            pass_at_1 = outcome_passes[0]
            pass_hat_k = all(outcome_passes)
            print(f"{case.name}: pass@1={pass_at_1} pass^{len(outcome_passes)}={pass_hat_k} tool_calls_ok={all(tool_passes)}")
            for i, trial in enumerate(result.trials):
                print(f"    trial {i}: decision={trial.decision} confidence={trial.confidence} tools={trial.tools_called}")

if __name__ == "__main__":
    asyncio.run(run_all())
```

**Step 3: Run it and read every transcript**

```bash
cd backend && python -m app.eval.report
```

Expected: some cases fail on the first run — per the evals framework, read *why* before assuming the agent is wrong. A case failing because the seed data doesn't actually contain what the grader expects is a broken task, not an incapable agent (Step 6/Step 7 in `AI-Agent-Evals.md`).

**Step 4: Commit**

```bash
git add backend/app/eval/suite.py backend/app/eval/report.py
git commit -m "feat: add 9 balanced eval cases and pass@1/pass^k reporting"
```

---

### Task 27: `POST /eval/run` endpoint

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_eval_endpoint.py`

**Step 1: Write the failing test**

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
    assert len(body["cases"]) == 9
    assert "pass_at_1" in body["cases"][0]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_endpoint.py -v -m integration`
Expected: FAIL — 404, route doesn't exist

**Step 3: Add to `backend/app/main.py`**

```python
from app.db import SessionLocal
from app.eval.suite import CASES
from app.eval.harness import run_case
from app.eval.graders import grade_outcome, grade_tool_calls

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

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_endpoint.py -v -m integration`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_eval_endpoint.py
git commit -m "feat: add POST /eval/run endpoint"
```

---

## What's next

This plan stops at a working backend: extraction, RAG, a tool-using agent, and an eval harness you can hit via `POST /eval/run`. The remaining pieces from `Project.MD` — `POST /invoices`, `GET /invoices`, approve/reject endpoints, and the React dashboard — get their own plan once you've run this one end-to-end and are happy with how the agent performs on the 9 cases.
