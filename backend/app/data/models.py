import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.data.db import Base


class IdentifierType(str, enum.Enum):
    isbn10 = "isbn10"
    isbn13 = "isbn13"
    other = "other"


class FileStatus(str, enum.Enum):
    inbox = "inbox"
    organised = "organised"
    review = "review"
    unidentified = "unidentified"
    duplicate = "duplicate"
    rejected = "rejected"


class FileStatusReason(str, enum.Enum):
    multi_parent = "multi_parent"
    no_parent = "no_parent"
    manual_drift = "manual_drift"
    parse_failed = "parse_failed"
    low_confidence = "low_confidence"
    previously_rejected = "previously_rejected"


class OperationAction(str, enum.Enum):
    move = "move"
    rename = "rename"
    move_and_rename = "move_and_rename"


class OperationStatus(str, enum.Enum):
    done = "done"
    undone = "undone"


class ReviewStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    corrected = "corrected"


class RuleType(str, enum.Enum):
    filename_pattern = "filename_pattern"
    author_alias = "author_alias"
    series_alias = "series_alias"


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    books: Mapped[list["Book"]] = relationship(back_populates="author")


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    aliases: Mapped[list["SeriesAlias"]] = relationship(back_populates="series")
    books: Mapped[list["Book"]] = relationship(back_populates="series")


class SeriesAlias(Base):
    __tablename__ = "series_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), nullable=False)
    alias: Mapped[str] = mapped_column(String, nullable=False)

    series: Mapped["Series"] = relationship(back_populates="aliases")


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_title: Mapped[str] = mapped_column(String, nullable=False)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id"))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"))
    series_number: Mapped[float | None] = mapped_column()
    description: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String)
    first_published: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    author: Mapped["Author | None"] = relationship(back_populates="books")
    series: Mapped["Series | None"] = relationship(back_populates="books")
    identifiers: Mapped[list["Identifier"]] = relationship(back_populates="book")
    files: Mapped[list["File"]] = relationship(back_populates="book")


class Identifier(Base):
    __tablename__ = "identifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), nullable=False)
    type: Mapped[IdentifierType] = mapped_column(Enum(IdentifierType), nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str | None] = mapped_column(String)

    book: Mapped["Book"] = relationship(back_populates="identifiers")


class File(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drive_file_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    drive_parent_id: Mapped[str | None] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[int | None] = mapped_column(ForeignKey("books.id"))
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus), nullable=False, default=FileStatus.inbox
    )
    status_reason: Mapped[FileStatusReason | None] = mapped_column(Enum(FileStatusReason))
    quality_score: Mapped[int | None] = mapped_column(Integer)
    discovered_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_processed_at: Mapped[datetime | None] = mapped_column()

    book: Mapped["Book | None"] = relationship(back_populates="files")
    metadata_sources: Mapped[list["MetadataSource"]] = relationship(back_populates="file")
    candidates: Mapped[list["BookCandidate"]] = relationship(back_populates="file")
    ai_decisions: Mapped[list["AIDecision"]] = relationship(back_populates="file")
    operations: Mapped[list["Operation"]] = relationship(back_populates="file")
    reviews: Mapped[list["Review"]] = relationship(back_populates="file")

    __table_args__ = (Index("ix_files_sha256_status", "sha256", "status"),)


class MetadataSource(Base):
    __tablename__ = "metadata_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(server_default=func.now())

    file: Mapped["File"] = relationship(back_populates="metadata_sources")


class BookCandidate(Base):
    __tablename__ = "book_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    author: Mapped[str | None] = mapped_column(String)
    series: Mapped[str | None] = mapped_column(String)
    series_number: Mapped[float | None] = mapped_column()
    source: Mapped[str] = mapped_column(String, nullable=False)
    confidence_component_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    file: Mapped["File"] = relationship(back_populates="candidates")


class AIDecision(Base):
    __tablename__ = "ai_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String, nullable=False)
    raw_response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    computed_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_reported_confidence: Mapped[float | None] = mapped_column()
    needs_human_review: Mapped[bool] = mapped_column(default=True)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    file: Mapped["File"] = relationship(back_populates="ai_decisions")

    __table_args__ = (Index("ix_ai_decisions_file_evidence", "file_id", "evidence_hash"),)


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(server_default=func.now())
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    action: Mapped[OperationAction] = mapped_column(Enum(OperationAction), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String)
    original_parent_id: Mapped[str | None] = mapped_column(String)
    new_name: Mapped[str | None] = mapped_column(String)
    new_parent_id: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    status: Mapped[OperationStatus] = mapped_column(
        Enum(OperationStatus), nullable=False, default=OperationStatus.done
    )
    dry_run: Mapped[bool] = mapped_column(nullable=False, default=False)

    file: Mapped["File"] = relationship(back_populates="operations")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus), nullable=False, default=ReviewStatus.pending
    )
    proposed_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correction_json: Mapped[dict | None] = mapped_column(JSON)
    resolved_at: Mapped[datetime | None] = mapped_column()

    file: Mapped["File"] = relationship(back_populates="reviews")


class LibraryRule(Base):
    __tablename__ = "library_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_type: Mapped[RuleType] = mapped_column(Enum(RuleType), nullable=False)
    pattern: Mapped[str] = mapped_column(String, nullable=False)
    resolution_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_from_review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class LocalFileStatus(str, enum.Enum):
    pending = "pending"
    copied = "copied"
    dismissed = "dismissed"


class LocalFile(Base):
    """A supported ebook file found under the watched local folder (e.g. a
    torrents download directory), tracked so a re-scan only surfaces
    genuinely new files — not the same ones offered (and copied or
    dismissed) on a previous pass."""

    __tablename__ = "local_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[LocalFileStatus] = mapped_column(
        Enum(LocalFileStatus), nullable=False, default=LocalFileStatus.pending
    )
    discovered_at: Mapped[datetime] = mapped_column(server_default=func.now())


__all__ = [
    "Author",
    "Series",
    "SeriesAlias",
    "Book",
    "Identifier",
    "File",
    "MetadataSource",
    "BookCandidate",
    "AIDecision",
    "Operation",
    "Review",
    "LibraryRule",
    "Setting",
    "LocalFile",
]
