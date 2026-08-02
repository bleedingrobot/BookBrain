import hashlib
import uuid

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.data.db import async_session_factory
from app.data.models import (
    AIDecision,
    BookCandidate,
    File,
    FileStatus,
    FileStatusReason,
    MetadataSource,
    Review,
    ReviewStatus,
)
from app.providers.drive.classify import classify_file
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.errors import EpubParseError
from app.providers.epub.parser import EpubEvidence, parse_epub_safely
from app.schemas.scan import ScanJobState, ScanJobStatus
from app.services.book_repository import resolve_book
from app.services.candidate_service import CandidateService, default_candidate_service
from app.services.identification_service import IdentificationResult, IdentificationService
from app.services.quality_service import score_quality
from app.services.sticky_resolution import find_rule_match, resolve_corrected_book_id


class ScanService:
    """Idempotent scan: lists the inbox folder, downloads+hashes+parses any
    Drive file not already in `files`, generates candidates, identifies the
    book, and upserts evidence/candidates/the resolved book. Re-running a
    scan only processes what's new (SPEC.md's "scanner as idempotent
    function" simplification for v1 — no webhooks yet).

    Threshold routing here only sets `files.status`/`status_reason` for the
    <85 confidence tiers (review / unidentified) — actual Drive move/rename
    for the ≥85 auto-eligible tier is Milestone 6's job. A structural issue
    (multi-parent/no-parent) always overrides confidence-based routing.

    A file whose content (sha256) exactly matches an already-known file is
    marked `duplicate` and short-circuits before parsing — same bytes means
    identical evidence, so identification is just copied from the primary.
    Every successfully-parsed file also gets a `quality_score` (§ file
    completeness, distinct from identification confidence).
    """

    def __init__(
        self,
        candidate_service: CandidateService | None = None,
        identification_service: IdentificationService | None = None,
    ) -> None:
        self._jobs: dict[str, ScanJobStatus] = {}
        self._candidate_service = candidate_service or default_candidate_service()
        self._identification_service = identification_service or IdentificationService()

    def create_job(self) -> ScanJobStatus:
        job_id = str(uuid.uuid4())
        status = ScanJobStatus(job_id=job_id, status=ScanJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> ScanJobStatus | None:
        return self._jobs.get(job_id)

    async def run_scan(self, job_id: str, creds: Credentials, folder_id: str) -> None:
        settings = get_settings()
        provider = DriveProvider(build_drive_service(creds))
        counts = {
            "new": 0,
            "flagged": 0,
            "duplicate": 0,
            "skipped_existing": 0,
            "skipped_too_large": 0,
            "failed": 0,
        }

        try:
            raw_files = provider.list_epub_files(folder_id)
        except Exception as exc:  # Drive API failure — nothing to salvage
            self._jobs[job_id] = ScanJobStatus(
                job_id=job_id, status=ScanJobState.failed, detail=str(exc)
            )
            return

        async with async_session_factory() as session:
            for raw in raw_files:
                await self._process_file(session, provider, raw, settings, counts)

        detail = (
            f"{counts['new']} new, {counts['flagged']} flagged for review, "
            f"{counts['duplicate']} duplicate, "
            f"{counts['skipped_existing']} already known, "
            f"{counts['skipped_too_large']} skipped (too large), "
            f"{counts['failed']} failed to parse"
        )
        self._jobs[job_id] = ScanJobStatus(job_id=job_id, status=ScanJobState.done, detail=detail)

    async def _process_file(
        self,
        session: AsyncSession,
        provider: DriveProvider,
        raw: dict,
        settings: Settings,
        counts: dict[str, int],
    ) -> None:
        existing = await session.execute(select(File).where(File.drive_file_id == raw["id"]))
        if existing.scalar_one_or_none() is not None:
            counts["skipped_existing"] += 1
            return

        declared_size = int(raw.get("size") or 0)
        if declared_size > settings.epub_max_total_bytes:
            counts["skipped_too_large"] += 1
            return

        status_reason = classify_file(raw)

        try:
            data = provider.download_file(raw["id"])
        except Exception:
            counts["failed"] += 1
            return

        sha256 = hashlib.sha256(data).hexdigest()

        primary_result = await session.execute(
            select(File).where(File.sha256 == sha256, File.status != FileStatus.duplicate)
        )
        primary = primary_result.scalars().first()
        if primary is not None:
            # SPEC.md §6: sha256 is indexed for exact-duplicate lookup. Same
            # bytes means identical evidence, so skip re-parsing entirely —
            # just copy the primary's identification and quality score,
            # unless this exact content has since been human-corrected, in
            # which case the correction is authoritative (§1).
            corrected_book_id = await resolve_corrected_book_id(session, sha256)
            session.add(
                File(
                    drive_file_id=raw["id"],
                    drive_parent_id=(raw.get("parents") or [None])[0],
                    filename=raw["name"],
                    sha256=sha256,
                    size_bytes=len(data),
                    status=FileStatus.duplicate,
                    status_reason=FileStatusReason(status_reason) if status_reason else None,
                    book_id=corrected_book_id if corrected_book_id is not None else primary.book_id,
                    quality_score=primary.quality_score,
                )
            )
            await session.commit()
            counts["duplicate"] += 1
            return

        file_row = File(
            drive_file_id=raw["id"],
            drive_parent_id=(raw.get("parents") or [None])[0],
            filename=raw["name"],
            sha256=sha256,
            size_bytes=len(data),
            status=FileStatus.review if status_reason else FileStatus.inbox,
            status_reason=FileStatusReason(status_reason) if status_reason else None,
        )

        try:
            evidence = parse_epub_safely(
                data,
                max_entry_bytes=settings.epub_max_entry_bytes,
                max_total_bytes=settings.epub_max_total_bytes,
                max_entries=settings.epub_max_entries,
                timeout_seconds=settings.epub_parse_timeout_seconds,
            )
        except EpubParseError:
            file_row.status = FileStatus.unidentified
            file_row.status_reason = FileStatusReason.parse_failed
            session.add(file_row)
            await session.commit()
            counts["failed"] += 1
            return

        file_row.quality_score = score_quality(evidence, len(data))

        session.add(file_row)
        await session.flush()

        for source in _evidence_to_metadata_sources(file_row.id, raw["name"], evidence):
            session.add(source)

        # A sha256 match on content that's already been human-corrected is
        # handled by the duplicate branch above (resolve_corrected_book_id)
        # — by construction, if a correction exists for this sha256, some
        # other File already has it, so the duplicate check already fired.
        identification = await find_rule_match(session, raw["name"], evidence)

        if identification is None:
            candidates = await self._candidate_service.generate_candidates(
                isbn13=evidence.isbn13,
                isbn10=evidence.isbn10,
                title=evidence.title,
                authors=", ".join(evidence.authors) if evidence.authors else None,
            )
            for candidate in candidates:
                session.add(
                    BookCandidate(
                        file_id=file_row.id,
                        title=candidate.title,
                        author=", ".join(candidate.authors) if candidate.authors else None,
                        series=candidate.series,
                        series_number=candidate.series_number,
                        source=candidate.source,
                        confidence_component_json={},
                    )
                )
            identification = await self._identification_service.identify(
                filename=raw["name"], evidence=evidence, candidates=candidates
            )

        book = await resolve_book(
            session,
            title=identification.title,
            author=identification.author,
            series=identification.series,
            series_number=identification.series_number,
            isbn13=evidence.isbn13,
            isbn10=evidence.isbn10,
        )
        file_row.book_id = book.id

        if status_reason is None:
            if identification.computed_confidence < settings.confidence_review_queue:
                file_row.status = FileStatus.unidentified
            elif identification.computed_confidence < settings.confidence_auto_flagged:
                file_row.status = FileStatus.review
                file_row.status_reason = FileStatusReason.low_confidence
            # >= confidence_auto_flagged stays FileStatus.inbox — auto-eligible,
            # actual move is Milestone 6. confidence_auto_organize (95) isn't a
            # separate behavioral gate in v1 — every organize op logs its
            # confidence to `operations`, so the 85-94 vs 95+ split is visible
            # in Activity without needing a distinct status.

        session.add(
            AIDecision(
                file_id=file_row.id,
                model=identification.model,
                prompt_hash=identification.prompt_hash,
                evidence_hash=identification.evidence_hash,
                raw_response_json=identification.raw_response,
                computed_confidence=identification.computed_confidence,
                ai_reported_confidence=identification.ai_reported_confidence,
                needs_human_review=identification.needs_human_review,
                reasoning_summary=identification.reasoning_summary,
            )
        )

        if file_row.status == FileStatus.review:
            session.add(
                Review(
                    file_id=file_row.id,
                    status=ReviewStatus.pending,
                    proposed_json=_proposed_json(identification),
                )
            )

        await session.commit()

        counts["flagged" if status_reason else "new"] += 1


def _proposed_json(identification: IdentificationResult) -> dict:
    return {
        "title": identification.title,
        "author": identification.author,
        "series": identification.series,
        "series_number": identification.series_number,
        "computed_confidence": identification.computed_confidence,
        "ai_reported_confidence": identification.ai_reported_confidence,
        "reasoning_summary": identification.reasoning_summary,
    }


def _evidence_to_metadata_sources(
    file_id: int, filename: str, evidence: EpubEvidence
) -> list[MetadataSource]:
    fields: list[tuple[str, str, str]] = [("filename", filename, "drive")]
    if evidence.title:
        fields.append(("title", evidence.title, "epub"))
    if evidence.authors:
        fields.append(("authors", ", ".join(evidence.authors), "epub"))
    if evidence.language:
        fields.append(("language", evidence.language, "epub"))
    if evidence.description:
        fields.append(("description", evidence.description, "epub"))
    if evidence.isbn13:
        fields.append(("isbn13", evidence.isbn13, "epub"))
    if evidence.isbn10:
        fields.append(("isbn10", evidence.isbn10, "epub"))
    if evidence.series:
        fields.append(("series", evidence.series, "epub"))
    if evidence.series_number is not None:
        fields.append(("series_number", str(evidence.series_number), "epub"))
    if evidence.text_snippet:
        fields.append(("text_snippet", evidence.text_snippet, "epub"))
    return [
        MetadataSource(file_id=file_id, field_name=name, value=value, source=source)
        for name, value, source in fields
    ]


_scan_service = ScanService()


def get_scan_service() -> ScanService:
    return _scan_service
