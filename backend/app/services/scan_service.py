import asyncio
import hashlib
import logging
import uuid
from pathlib import Path

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.data.db import async_session_factory
from app.data.repositories.settings_repository import SettingsRepository
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
from app.providers.convert.calibre import convert_to_epub, is_convertible
from app.providers.drive.classify import classify_file, is_supported_ebook
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.providers.epub.errors import EpubParseError
from app.providers.epub.parser import EpubEvidence, parse_epub_safely
from app.schemas.scan import ScanFailure, ScanJobState, ScanJobStatus
from app.services.book_repository import get_book_write_lock, resolve_book
from app.services.candidate_service import CandidateService, default_candidate_service
from app.services.drive_service import DriveService
from app.services.duplicate_service import detect_same_book_duplicates
from app.services.identification_service import IdentificationResult, IdentificationService
from app.services.organize_service import get_organize_dry_run, get_organize_service
from app.services.quality_service import score_quality
from app.services.sticky_resolution import find_rule_match, resolve_corrected_book_id

logger = logging.getLogger(__name__)

# How many files a scan works on at once. Each file's slow work (Drive
# download, EPUB parse, metadata-provider lookups, the AI identification
# call) is independent of every other file's — bounded concurrency turns a
# batch that used to run one file at a time, each waiting out its own
# network round trips, into several overlapping at once. DB access is the
# one thing that *isn't* safe to run concurrently (a shared AsyncSession
# can't be used from multiple coroutines, and unguarded concurrent
# find-or-create on Author/Series would race the same way the organize
# folder-cache race did) — see _process_file's db_lock.
_SCAN_CONCURRENCY = 6


class ScanService:
    """Idempotent scan: lists the inbox folder, downloads+hashes+parses any
    Drive file not already in `files`, generates candidates, identifies the
    book, and upserts evidence/candidates/the resolved book. Re-running a
    scan only processes what's new (SPEC.md's "scanner as idempotent
    function" simplification for v1 — no webhooks yet).

    .epub and .kpub are processed as-is; .mobi and .rtf are converted to
    epub first via Calibre's ebook-convert CLI, replacing the Drive file's
    content/name in place (same drive_file_id) before anything else runs.
    The inbox listing is unfiltered by Drive mimeType (kpub has no
    reliable one), and anything that's neither a supported nor a
    convertible extension is trashed on sight rather than left in the
    book dump folder or silently ignored.

    Threshold routing here only sets `files.status`/`status_reason` for the
    <85 confidence tier (always `review`, always actionable via the Review
    Queue) — actual Drive move/rename for the ≥85 auto-eligible tier is
    Milestone 6's job. A structural issue (multi-parent/no-parent) always
    overrides confidence-based routing. `unidentified` is reserved for
    genuine failures (EPUB parse errors) with nothing to review against.

    A file whose content (sha256) exactly matches an already-known file is
    marked `duplicate` and short-circuits before parsing — same bytes means
    identical evidence, so identification is just copied from the primary.
    Every successfully-parsed file also gets a `quality_score` (§ file
    completeness, distinct from identification confidence).

    `run_rebuild` is the same pipeline pointed at the organized library
    folder instead of the inbox, for reconstructing `files`/`books` from
    what Drive actually has on disk (see its docstring).

    A successful run_scan finishes by auto-organizing whatever landed in
    the ≥85 auto-eligible tier — the manual "Organize" button used to be
    the only way to move those, which meant every scan left work sitting
    in the inbox for no reason once you'd stopped actually reviewing the
    button before clicking it. Respects the same ORGANIZE_DRY_RUN setting
    the manual button does, and is silently skipped if no library folder
    is configured yet.
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
            "removed_non_ebook": 0,
            "converted": 0,
        }
        failures: list[ScanFailure] = []

        try:
            # Every file in the inbox, not just ebooks — anything that isn't
            # a supported .epub/.kpub or a convertible .mobi/.rtf gets
            # removed from the book dump by _process_file below, not just
            # ignored.
            raw_files = await asyncio.to_thread(provider.list_files_in_folder, folder_id)
        except Exception as exc:  # Drive API failure — nothing to salvage
            self._jobs[job_id] = ScanJobStatus(
                job_id=job_id, status=ScanJobState.failed, detail=str(exc)
            )
            return

        await self._process_batch(provider, raw_files, settings, counts, failures)

        async with async_session_factory() as session:
            same_book_duplicates = await detect_same_book_duplicates(session)
            await session.commit()

        organized = await self._auto_organize(creds, provider)

        detail = (
            f"{counts['new']} new, {counts['flagged']} flagged for review, "
            f"{counts['duplicate']} duplicate, "
            f"{same_book_duplicates} same-book duplicate, "
            f"{counts['skipped_existing']} already known, "
            f"{counts['skipped_too_large']} skipped (too large), "
            f"{counts['removed_non_ebook']} non-ebook files removed, "
            f"{counts['converted']} converted to epub, "
            f"{organized} auto-organized, "
            f"{counts['failed']} failed to parse"
        )
        self._jobs[job_id] = ScanJobStatus(
            job_id=job_id, status=ScanJobState.done, detail=detail, failures=failures
        )

    async def run_rebuild(self, job_id: str, creds: Credentials, library_root_folder_id: str) -> None:
        """Walks the organized library folder tree (not the inbox) and
        rebuilds `files`/`books`/etc. from what's actually sitting there —
        for recovering after a Clear Library wipe, or reconciling drift.
        Every file found is trusted as already-placed: it's parsed and
        identified the same way as a normal scan, but lands directly on
        `organised` instead of going through review/auto-organize routing,
        since nothing here is waiting to be moved."""
        settings = get_settings()
        provider = DriveProvider(build_drive_service(creds))
        counts = {
            "new": 0,
            "flagged": 0,
            "duplicate": 0,
            "skipped_existing": 0,
            "skipped_too_large": 0,
            "failed": 0,
            "removed_non_ebook": 0,
            "converted": 0,
        }
        failures: list[ScanFailure] = []

        try:
            raw_files = await asyncio.to_thread(provider.list_epub_files_recursive, library_root_folder_id)
        except Exception as exc:  # Drive API failure — nothing to salvage
            self._jobs[job_id] = ScanJobStatus(
                job_id=job_id, status=ScanJobState.failed, detail=str(exc)
            )
            return

        await self._process_batch(provider, raw_files, settings, counts, failures, already_organised=True)

        async with async_session_factory() as session:
            same_book_duplicates = await detect_same_book_duplicates(session)
            await session.commit()

        detail = (
            f"{counts['new']} rebuilt, {counts['duplicate']} duplicate, "
            f"{same_book_duplicates} same-book duplicate, "
            f"{counts['skipped_existing']} already known, "
            f"{counts['skipped_too_large']} skipped (too large), "
            f"{counts['failed']} failed to parse"
        )
        self._jobs[job_id] = ScanJobStatus(
            job_id=job_id, status=ScanJobState.done, detail=detail, failures=failures
        )

    async def _process_batch(
        self,
        provider: DriveProvider,
        raw_files: list[dict],
        settings: Settings,
        counts: dict[str, int],
        failures: list[ScanFailure],
        *,
        already_organised: bool = False,
    ) -> None:
        # A shared, process-wide lock — not one scoped to this batch. Two
        # scan/rebuild jobs running at once (or this batch's auto-organize
        # racing a manual organize/review correction) must serialize on the
        # *same* lock, or each job's own private lock only stops races
        # within itself and the fuzzy find-or-create race reopens at the
        # job level instead of the file level.
        db_lock = get_book_write_lock()
        semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)

        async def process_one(raw: dict) -> None:
            async with semaphore:
                await self._process_file_safely(
                    provider, raw, settings, counts, failures, db_lock, already_organised=already_organised
                )

        await asyncio.gather(*(process_one(raw) for raw in raw_files))

    async def _auto_organize(self, creds: Credentials, provider: DriveProvider) -> int:
        async with async_session_factory() as session:
            settings_repo = SettingsRepository(session)
            dry_run = await get_organize_dry_run(settings_repo)
            library = await DriveService.get_library_folder_config(settings_repo)

        if library is None:
            return 0

        counts, _failures = await get_organize_service().organize_eligible_files(
            provider=provider if not dry_run else None,
            library_root_folder_id=library.folder_id,
            dry_run=dry_run,
        )
        return counts["organized"]

    async def _process_file_safely(
        self,
        provider: DriveProvider,
        raw: dict,
        settings: Settings,
        counts: dict[str, int],
        failures: list[ScanFailure],
        db_lock: asyncio.Lock,
        *,
        already_organised: bool = False,
    ) -> None:
        """One bad file (a race on drive_file_id from an overlapping job, an
        unexpected provider/DB error, anything) must never take down the
        whole batch silently — without this, an exception here left the job
        stuck at "running" forever with no error surfaced anywhere."""
        try:
            await self._process_file(
                provider, raw, settings, counts, failures, db_lock, already_organised=already_organised
            )
        except Exception as exc:
            counts["failed"] += 1
            failures.append(ScanFailure(filename=raw["name"], reason=f"unexpected error: {exc}"))

    async def _process_file(
        self,
        provider: DriveProvider,
        raw: dict,
        settings: Settings,
        counts: dict[str, int],
        failures: list[ScanFailure],
        db_lock: asyncio.Lock,
        *,
        already_organised: bool = False,
    ) -> None:
        if not is_supported_ebook(raw["name"]) and not is_convertible(raw["name"]):
            # Only reachable from run_scan's broad inbox listing — run_rebuild
            # feeds this from an already-ebook-filtered recursive listing, so
            # nothing in the organized library folder is ever touched here.
            await asyncio.to_thread(provider.trash_file, raw["id"])
            counts["removed_non_ebook"] += 1
            return

        # Every DB touchpoint below opens its own short-lived session under
        # db_lock — a single AsyncSession isn't safe to use from multiple
        # concurrently-running files, and the fuzzy find-or-create in
        # resolve_book races the same way organize's folder cache would
        # without its lock (two new books by the same not-yet-seen author,
        # processed at once, could each decide the author doesn't exist yet
        # and both create it). Locking just the DB sections — not the whole
        # function — still lets every file's download/parse/candidates/AI
        # call (the actual slow part) run fully concurrently.
        async with db_lock, async_session_factory() as session:
            existing = await session.execute(select(File).where(File.drive_file_id == raw["id"]))
            if existing.scalar_one_or_none() is not None:
                counts["skipped_existing"] += 1
                return

        declared_size = int(raw.get("size") or 0)
        if declared_size > settings.epub_max_total_bytes:
            counts["skipped_too_large"] += 1
            return

        status_reason = classify_file(raw)

        # mobi/rtf never gets processed as-is — Calibre converts it to epub
        # first, and the Drive file's content/name are updated in place
        # (same drive_file_id, so everything downstream — dedup, organize,
        # the works — treats it exactly like a file that was always epub).
        # Left alone (not trashed) on failure so it can be retried or
        # inspected instead of silently vanishing.
        converted_data: bytes | None = None
        if is_convertible(raw["name"]):
            try:
                source_bytes = await asyncio.to_thread(provider.download_file, raw["id"])
                converted_data = await convert_to_epub(
                    source_bytes,
                    source_filename=raw["name"],
                    binary=settings.ebook_convert_binary,
                    timeout_seconds=settings.ebook_convert_timeout_seconds,
                )
            except Exception as exc:
                logger.exception("conversion failed for %s", raw["name"])
                counts["failed"] += 1
                failures.append(
                    ScanFailure(filename=raw["name"], reason=f"conversion to epub failed: {exc}")
                )
                return

            new_name = f"{Path(raw['name']).stem}.epub"
            await asyncio.to_thread(provider.update_file_content, raw["id"], new_name=new_name, data=converted_data)
            raw = {**raw, "name": new_name}
            counts["converted"] += 1

        try:
            data = (
                converted_data
                if converted_data is not None
                else await asyncio.to_thread(provider.download_file, raw["id"])
            )
        except Exception as exc:
            counts["failed"] += 1
            failures.append(ScanFailure(filename=raw["name"], reason=f"download failed: {exc}"))
            return

        sha256 = hashlib.sha256(data).hexdigest()

        async with db_lock, async_session_factory() as session:
            # SPEC.md §6: sha256 is indexed for exact-duplicate lookup. Same
            # bytes means identical evidence, so skip re-parsing entirely —
            # just copy the primary's identification and quality score,
            # unless this exact content has since been human-corrected, in
            # which case the correction is authoritative (§1).
            primary = await _find_primary_by_sha256(session, sha256)
            if primary is not None:
                await _insert_duplicate_file(session, raw, sha256, data, status_reason, primary)
                await session.commit()
                counts["duplicate"] += 1
                return

        try:
            evidence = parse_epub_safely(
                data,
                max_entry_bytes=settings.epub_max_entry_bytes,
                max_total_bytes=settings.epub_max_total_bytes,
                max_entries=settings.epub_max_entries,
                timeout_seconds=settings.epub_parse_timeout_seconds,
            )
        except EpubParseError as exc:
            async with db_lock, async_session_factory() as session:
                session.add(
                    File(
                        drive_file_id=raw["id"],
                        drive_parent_id=(raw.get("parents") or [None])[0],
                        filename=raw["name"],
                        sha256=sha256,
                        size_bytes=len(data),
                        status=FileStatus.unidentified,
                        status_reason=FileStatusReason.parse_failed,
                    )
                )
                await session.commit()
            counts["failed"] += 1
            failures.append(ScanFailure(filename=raw["name"], reason=str(exc)))
            return

        quality_score = score_quality(evidence, len(data))

        # Metadata sources/candidates are built into ORM rows here (cheap,
        # no I/O) but not added to any session until the final DB section —
        # binding `.file` only happens once file_row exists there.
        metadata_sources = _evidence_to_metadata_sources(raw["name"], evidence)

        # A sha256 match on content that's already been human-corrected is
        # handled by the duplicate branch above (resolve_corrected_book_id)
        # — by construction, if a correction exists for this sha256, some
        # other File already has it, so the duplicate check already fired.
        async with db_lock, async_session_factory() as session:
            identification = await find_rule_match(session, raw["name"], evidence)

        candidate_rows: list[BookCandidate] = []
        if identification is None:
            candidates = await self._candidate_service.generate_candidates(
                isbn13=evidence.isbn13,
                isbn10=evidence.isbn10,
                title=evidence.title,
                authors=", ".join(evidence.authors) if evidence.authors else None,
            )
            candidate_rows = [
                BookCandidate(
                    title=candidate.title,
                    author=", ".join(candidate.authors) if candidate.authors else None,
                    series=candidate.series,
                    series_number=candidate.series_number,
                    source=candidate.source,
                    confidence_component_json={},
                )
                for candidate in candidates
            ]
            identification = await self._identification_service.identify(
                filename=raw["name"], evidence=evidence, candidates=candidates
            )

        # Everything from here is DB-only — no more network calls — so the
        # lock is held only briefly despite doing the fuzzy Author/Series
        # find-or-create that must not race across concurrently-processing
        # files.
        async with db_lock, async_session_factory() as session:
            # Re-check for a primary here, not just at the earlier sha256
            # check: this coroutine just spent the parse/candidate/AI-identify
            # work (all of it deliberately unlocked, for concurrency) since
            # that check ran. A concurrent coroutine processing the exact same
            # content could have become the primary in the meantime — without
            # this re-check, both coroutines see "no primary yet" and both
            # insert themselves as one, so two byte-identical uploads land as
            # two separate books instead of one primary + one duplicate.
            primary = await _find_primary_by_sha256(session, sha256)
            if primary is not None:
                await _insert_duplicate_file(session, raw, sha256, data, status_reason, primary)
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
                quality_score=quality_score,
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
                if already_organised:
                    # Already sitting in the library folder — trust it, don't
                    # route through review/auto-organize thresholds again.
                    file_row.status = FileStatus.organised
                elif identification.computed_confidence < settings.confidence_auto_flagged:
                    # Below the auto-eligible bar, always goes to the review
                    # queue — never a dead-end `unidentified`. confidence_review_
                    # queue no longer forks to a separate unreviewable status: an
                    # AI call with thin evidence (no ISBN, one provider) can
                    # still be correct, and the reviewer sees the actual
                    # computed_confidence number to judge how much to trust it.
                    # `unidentified` is reserved for genuine failures (parse
                    # errors) where there's nothing to review against.
                    file_row.status = FileStatus.review
                    file_row.status_reason = FileStatusReason.low_confidence
                # >= confidence_auto_flagged stays FileStatus.inbox — auto-eligible,
                # picked up by run_scan's auto-organize pass right after this batch
                # finishes. confidence_auto_organize (95) isn't a separate
                # behavioral gate in v1 — every organize op logs its confidence to
                # `operations`, so the 85-94 vs 95+ split is visible in Activity
                # without needing a distinct status.

            ai_decision = AIDecision(
                model=identification.model,
                prompt_hash=identification.prompt_hash,
                evidence_hash=identification.evidence_hash,
                raw_response_json=identification.raw_response,
                computed_confidence=identification.computed_confidence,
                ai_reported_confidence=identification.ai_reported_confidence,
                needs_human_review=identification.needs_human_review,
                reasoning_summary=identification.reasoning_summary,
            )

            review_row = None
            if file_row.status == FileStatus.review:
                review_row = Review(status=ReviewStatus.pending, proposed_json=_proposed_json(identification))

            # `.file = file_row` (not `.file_id = file_row.id`) lets SQLAlchemy
            # resolve every FK itself at flush time, in one round trip, without
            # needing file_row's id up front.
            for source in metadata_sources:
                source.file = file_row
            for candidate in candidate_rows:
                candidate.file = file_row
            ai_decision.file = file_row
            if review_row is not None:
                review_row.file = file_row

            session.add(file_row)
            session.add_all(metadata_sources)
            session.add_all(candidate_rows)
            session.add(ai_decision)
            if review_row is not None:
                session.add(review_row)

            await session.commit()

        counts["flagged" if status_reason else "new"] += 1


async def _find_primary_by_sha256(session: AsyncSession, sha256: str) -> File | None:
    result = await session.execute(
        select(File).where(File.sha256 == sha256, File.status != FileStatus.duplicate)
    )
    return result.scalars().first()


async def _insert_duplicate_file(
    session: AsyncSession,
    raw: dict,
    sha256: str,
    data: bytes,
    status_reason: str | None,
    primary: File,
) -> None:
    # A structural reason from this file's own Drive placement always wins;
    # short of that, a primary that was itself rejected means this is a
    # re-upload of already-rejected content, worth surfacing as such rather
    # than a bare "duplicate".
    corrected_book_id = await resolve_corrected_book_id(session, sha256)
    duplicate_reason = (
        FileStatusReason(status_reason)
        if status_reason
        else (FileStatusReason.previously_rejected if primary.status == FileStatus.rejected else None)
    )
    session.add(
        File(
            drive_file_id=raw["id"],
            drive_parent_id=(raw.get("parents") or [None])[0],
            filename=raw["name"],
            sha256=sha256,
            size_bytes=len(data),
            status=FileStatus.duplicate,
            status_reason=duplicate_reason,
            book_id=corrected_book_id if corrected_book_id is not None else primary.book_id,
            quality_score=primary.quality_score,
        )
    )


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


def _evidence_to_metadata_sources(filename: str, evidence: EpubEvidence) -> list[MetadataSource]:
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
        MetadataSource(field_name=name, value=value, source=source) for name, value, source in fields
    ]


_scan_service = ScanService()


def get_scan_service() -> ScanService:
    return _scan_service
