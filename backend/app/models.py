import uuid
from datetime import datetime, date

from sqlalchemy import (
    CheckConstraint,
    String,
    Text,
    Numeric,
    Date,
    DateTime,
    ForeignKey,
    Float,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Document(Base):
    """A chunk of the AP policy corpus. Policy only -- invoice text is never embedded."""

    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = uuid_pk()
    section: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "II. Policy"
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Width must match rag.embeddings.EMBED_DIM.
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)


class PurchaseOrder(Base):
    """Stands in for the ERP's purchase order records."""

    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = uuid_pk()
    po_number: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # ISO 4217. Constrained uppercase so 'eur' and 'EUR' cannot compare as a mismatch.
    currency: Mapped[str] = mapped_column(String(3), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "currency IS NULL OR currency = upper(currency)",
            name="ck_purchase_orders_currency_upper",
        ),
    )


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String, nullable=False)
    bank_details: Mapped[str] = mapped_column(String, nullable=True)
    # 'pending_approval' | 'active'. Only active vendors resolve in lookup_vendor.
    # Defaults to pending so a vendor never becomes payable by omission.
    approval_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending_approval"
    )
    created_by: Mapped[str] = mapped_column(String, nullable=False, server_default="human")  # 'agent' | 'human'


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = uuid_pk()
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    # The payee as printed, written at extraction time. Unverified by construction:
    # vendor_id is the resolved identity, this is only what the document claimed.
    # Without it an unresolved payee records nowhere who it claims to be from.
    extracted_vendor_name: Mapped[str] = mapped_column(String, nullable=True)
    invoice_number: Mapped[str] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=True)  # ISO 4217
    due_date: Mapped[date] = mapped_column(Date, nullable=True)
    po_number: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    raw_pdf_path: Mapped[str] = mapped_column(String, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    line_items: Mapped[list["LineItem"]] = relationship(back_populates="invoice")

    __table_args__ = (
        CheckConstraint(
            "currency IS NULL OR currency = upper(currency)",
            name="ck_invoices_currency_upper",
        ),
    )


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
    # 'running' | 'complete'. No default on purpose: a forgotten insert should
    # raise rather than let a running row read as finished.
    status: Mapped[str] = mapped_column(String, nullable=False)
    transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # "agent" | "human"
    action: Mapped[str] = mapped_column(String, nullable=False)
    before_state: Mapped[dict] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # clock_timestamp(), not now(): now() is transaction-start time, so rows
    # written in one transaction would tie in a log that exists for its order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
