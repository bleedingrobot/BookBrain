import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
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
    same_book = "same_book"


class OperationAction(str, enum.Enum):
    move = "move"
    rename = "rename"
    move_and_rename = "move_and_rename"
    # Rewrote the .epub's embedded OPF metadata (title/author/series/cover)
    # in place. Not app-undoable — we don't keep the original bytes (Drive's
    # own revision history is the fallback). See operation_service.undo_operation.
    write_metadata = "write_metadata"
    # A file moved as part of a series merge. Logged for the Activity trail
    # but deliberately NOT auto-undoable: the merge deletes the emptied source
    # Series row + folder, so a naive "move it back" lands the file in a
    # deleted folder with a stale book.series. See operation_service.
    series_merge = "series_merge"


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

    # prompts/27 Part 2 — Hardcover's recent + near-future books for this
    # author, refreshed by hardcover_new_releases_service on a schedule.
    #   {books: [{title, releaseDate, isbn13, category, genres}]}
    # hardcover_synced_at drives the "needs a refresh?" query.
    hardcover_json: Mapped[dict | None] = mapped_column(JSON)
    hardcover_synced_at: Mapped[datetime | None] = mapped_column()
    # prompts/28 Phase 1 — Hardcover's canonical "person id" for this author
    # (its id after following canonical_id then alias_id). Two Author rows that
    # resolve to the same value are the same person even with no shared book
    # ("Iain M. Banks" / "Iain Banks"). Written by the same per-author pass.
    hardcover_person_id: Mapped[int | None] = mapped_column()

    books: Mapped[list["Book"]] = relationship(back_populates="author")


class Series(Base):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # prompts/25 Phase 2 — Hardcover's canonical view of this series, refreshed
    # by hardcover_series_service on a schedule. hardcover_json:
    #   {id, name, slug, primaryCount,
    #    books: [{position, title, releaseDate?, isbn13?}],
    #    match: "auto" | "manual" | "none"}
    # "none" = we searched and found no good match (don't re-search nightly);
    # "manual" = James pinned hardcover_json.id (never re-matched, list still
    # refreshed). hardcover_synced_at drives the "needs a refresh?" query.
    hardcover_json: Mapped[dict | None] = mapped_column(JSON)
    hardcover_synced_at: Mapped[datetime | None] = mapped_column()

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

    # prompts/25 Phase 3 — Hardcover's "readers also liked" for this book,
    # refreshed by hardcover_recs_service on a schedule.
    #   {id: <hardcover book id>, similar: [{title, author, isbn13}]}  (top ~15)
    # hardcover_synced_at drives the "needs a refresh?" query.
    hardcover_json: Mapped[dict | None] = mapped_column(JSON)
    hardcover_synced_at: Mapped[datetime | None] = mapped_column()

    # prompts/29 — a local sentence embedding (all-MiniLM-L6-v2, 384-dim
    # float32, L2-normalised, stored raw) of this book's title/author/series +
    # blurb, for the library-viewer's semantic search. embedding_hash is a
    # hash of the embed input so a refresh only re-embeds what changed;
    # embedding_model guards against a model swap.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_hash: Mapped[str | None] = mapped_column(String)
    embedding_model: Mapped[str | None] = mapped_column(String)

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
    # The content hash this file had *before* BookBrain rewrote its embedded
    # metadata (see metadata_writeback_service). NULL if never rewritten.
    # Exact-duplicate detection and sticky corrections (SPEC §1) match on
    # this too, so a re-upload of the pristine original still resolves to
    # the same book / inherits the same human correction.
    original_sha256: Mapped[str | None] = mapped_column(String, index=True)
    # Set once the embedded OPF metadata has been written to match the
    # resolved book — a hash of (title, author, series, series_number). The
    # writeback job skips a file whose key already matches, so re-runs cause
    # no hash churn; a later correction changes the key and it's picked up.
    embedded_metadata_key: Mapped[str | None] = mapped_column(String)
    # Hex string of a 64-bit perceptual hash (imagehash.phash) of this file's
    # organised cover thumbnail — 16 hex chars, NULL until a cover has been
    # generated and NULL for a .nocover file. Used by Library Audit to flag
    # different-identified files with near-identical cover art (a re-upload
    # with rewritten metadata that sha256 dedup can't catch). Not indexed:
    # the audit is a full O(n²) scan of a few thousand short strings.
    cover_phash: Mapped[str | None] = mapped_column(String)
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


class WishlistStatus(str, enum.Enum):
    wanted = "wanted"
    acquired = "acquired"


class WishlistItem(Base):
    """A book the user wants but doesn't own yet. `raw_request` is what they
    typed; the rest is what Claude + Google Books resolved it to. Flips to
    `acquired` (with `acquired_file_id`) once a matching book turns up in
    the library — see wishlist_service.reconcile."""

    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_request: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String)
    series: Mapped[str | None] = mapped_column(String)
    series_number: Mapped[float | None] = mapped_column()
    isbn13: Mapped[str | None] = mapped_column(String)
    cover_url: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[WishlistStatus] = mapped_column(
        Enum(WishlistStatus), nullable=False, default=WishlistStatus.wanted
    )
    acquired_at: Mapped[datetime | None] = mapped_column()
    acquired_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class JobRunStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failed = "failed"


class JobRun(Base):
    """Audit trail for a whole-pipeline run (currently only `kind="nightly"`).
    Unlike the in-memory `ScanService._jobs` tracker, this survives a server
    restart, so the morning-after Dashboard can show what the overnight run
    did — and a `running` row doubles as a coarse "a pipeline run is active"
    flag that keeps a scheduled run and a standalone `python -m app.jobs.nightly`
    from stepping on each other (see job_run_service.has_active_run)."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False)  # scheduler | cli | manual
    status: Mapped[JobRunStatus] = mapped_column(
        Enum(JobRunStatus), nullable=False, default=JobRunStatus.running
    )
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column()
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_job_runs_kind_started", "kind", "started_at"),)


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


class DismissedReidentFlag(Base):
    """A book the Bulk Re-identify Audit flagged as diverging from its stored
    identification that James has reviewed and decided not to act on (a
    false positive, or a divergence he's chosen to leave). The reident
    report filters these out by book id at read time — same pattern as
    DismissedAuditCluster. Keyed on book id alone: the report row *is* a
    book, and any later real change to that book (a /correct) is a
    different question that a fresh rebuild will re-surface if it still
    diverges."""

    __tablename__ = "dismissed_reident_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("book_id", name="uq_dismissed_reident_flag"),)


class AuditClusterKind(str, enum.Enum):
    series = "series"
    author = "author"


class DismissedAuditCluster(Base):
    """A Library Audit "possibly split" cluster the user has already
    reviewed and decided isn't worth re-flagging (a false positive, or a
    real split they've chosen to leave as-is) — audit_library filters these
    out by exact member-id-set match. member_ids_key is the cluster's
    member ids, sorted and comma-joined (e.g. "6,399") — if the cluster's
    membership changes later (e.g. one member gets merged away elsewhere),
    that's a different key and the dismissal naturally stops applying,
    since it's genuinely a different question being asked."""

    __tablename__ = "dismissed_audit_clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[AuditClusterKind] = mapped_column(Enum(AuditClusterKind), nullable=False)
    member_ids_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("kind", "member_ids_key", name="uq_dismissed_audit_cluster"),)


class AcquisitionStatus(str, enum.Enum):
    pending = "pending"  # a candidate has been found, waiting for James to approve
    approved = "approved"  # downloaded and pushed into the Drive inbox
    skipped = "skipped"  # James dismissed it — don't re-offer until reset
    no_match = "no_match"  # searched OpenBooks, nothing good enough (re-tried each refresh)
    failed = "failed"  # a download attempt failed (re-tried each refresh)


class AcquisitionCandidate(Base):
    """prompts/37 — an OpenBooks search result matched to an unfilled request
    on the viewer's `bookbrain-wishlist.json` (status "wanted"). Populated by
    `acquisition_service.refresh_candidates` (the admin "Search open requests"
    button and a nightly step); James approves one per request from the Find a
    Book page, which downloads it and marks the wishlist item "sourced".

    Keyed on the wishlist item id. EPUB candidates only, by design."""

    __tablename__ = "acquisition_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    request_title: Mapped[str] = mapped_column(String, nullable=False)
    request_author: Mapped[str | None] = mapped_column(String)
    status: Mapped[AcquisitionStatus] = mapped_column(
        Enum(AcquisitionStatus), nullable=False, default=AcquisitionStatus.pending
    )
    # The current best pick (null for no_match).
    candidate_full: Mapped[str | None] = mapped_column(String)
    candidate_title: Mapped[str | None] = mapped_column(String)
    candidate_author: Mapped[str | None] = mapped_column(String)
    candidate_format: Mapped[str | None] = mapped_column(String)
    candidate_size: Mapped[str | None] = mapped_column(String)
    candidate_server: Mapped[str | None] = mapped_column(String)
    score: Mapped[float | None] = mapped_column(Float)
    # Other plausible EPUBs, best first: [{full,title,author,format,size,server,score}].
    alternatives_json: Mapped[list | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column()


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
    "JobRunStatus",
    "JobRun",
    "LocalFile",
    "WishlistItem",
    "AuditClusterKind",
    "DismissedAuditCluster",
    "DismissedReidentFlag",
    "AcquisitionStatus",
    "AcquisitionCandidate",
]
