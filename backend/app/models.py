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
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False)  # "invoice" | "policy"
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
