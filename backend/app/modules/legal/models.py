from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UUIDPrimaryKey

DOC_TYPES = ("terms", "privacy", "host_terms")
CONSENT_REQUIRED_FOR_ALL = ("terms", "privacy")


class LegalDocument(UUIDPrimaryKey, Base):
    __tablename__ = "legal_documents"
    __table_args__ = (UniqueConstraint("doc_type", "version", name="uq_legal_doc_version"),)

    doc_type: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
