import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings_keys import TORRENTS_AUTOMATCH_ENABLED
from app.data.models import AcquisitionCandidate, AcquisitionStatus, LocalFile, LocalFileStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.providers.convert.calibre import is_convertible
from app.providers.drive.classify import is_supported_ebook
from app.providers.drive.provider import DriveProvider
from app.providers.filename.parser import parse_book_filename
from app.services import acquisition_service, inbox_upload_service

logger = logging.getLogger(__name__)


def _is_watchable(filename: str) -> bool:
    return is_supported_ebook(filename) or is_convertible(filename)


async def scan_local_folder(session: AsyncSession, root: str) -> list[LocalFile]:
    """Walks `root` recursively, recording every not-yet-seen .epub/.kpub/
    .cbz/.cbr/.mobi/.rtf/.txt file as a pending LocalFile row (keyed by absolute path, so a
    re-scan never re-offers the same file twice). Returns every row still
    pending — both newly discovered this pass and anything left over from
    an earlier scan the user hasn't copied or dismissed yet."""
    root_path = Path(root)
    if root_path.is_dir():
        for path in root_path.rglob("*"):
            if not path.is_file() or not _is_watchable(path.name):
                continue
            resolved = str(path.resolve())
            existing = await session.execute(select(LocalFile).where(LocalFile.path == resolved))
            if existing.scalar_one_or_none() is not None:
                continue
            session.add(
                LocalFile(
                    path=resolved,
                    filename=path.name,
                    size_bytes=path.stat().st_size,
                    status=LocalFileStatus.pending,
                )
            )
        await session.commit()

    return await list_pending(session)


async def list_pending(session: AsyncSession) -> list[LocalFile]:
    result = await session.execute(select(LocalFile).where(LocalFile.status == LocalFileStatus.pending))
    return list(result.scalars().all())


async def copy_to_drive(
    session: AsyncSession, file_ids: list[int], provider: DriveProvider, inbox_folder_id: str
) -> dict[str, int]:
    copied = 0
    failed = 0
    for file_id in file_ids:
        row = await session.get(LocalFile, file_id)
        if row is None or row.status != LocalFileStatus.pending:
            continue
        try:
            await inbox_upload_service.upload_local_file_to_inbox(
                Path(row.path), row.filename, provider, inbox_folder_id
            )
        except Exception:
            # Broad on purpose — the validation step (AcquisitionError) and
            # the Drive upload itself (arbitrary provider/API errors) both
            # land here. Never delete row.path either way — it's the user's
            # own torrents-folder file, not a scratch temp download; a
            # rejected/failed file just stays pending so it can be inspected
            # or retried.
            failed += 1
            continue
        row.status = LocalFileStatus.copied
        copied += 1
    await session.commit()
    return {"copied": copied, "failed": failed}


async def match_against_wishlist(
    session: AsyncSession,
    rows: list[LocalFile],
    drive_provider: DriveProvider,
    inbox_folder_id: str,
    library_folder_id: str,
) -> None:
    """ROADMAP.md "close the acquisition loop": guess a filename's
    title/author (parse_book_filename — the same deterministic parser
    identification already uses) and score it against the open wishlist /
    want-to-read / list targets, reusing acquisition_service's own matching
    (gather_acquisition_targets + score_candidate) rather than a second
    implementation. A strong match populates `matched_*` for the UI to show
    next to the file; it's only auto-uploaded to the inbox when
    TORRENTS_AUTOMATCH_ENABLED is on — otherwise every match, however
    strong, still waits for a human to hit "copy"."""
    if not rows:
        return

    targets = await acquisition_service.gather_acquisition_targets(drive_provider, library_folder_id)
    if not targets:
        return

    automatch_on = (await SettingsRepository(session).get(TORRENTS_AUTOMATCH_ENABLED)) == "true"

    for row in rows:
        guess = parse_book_filename(row.filename)
        if not guess.usable or not guess.title:
            continue

        # A real size (not "N/A") avoids score_candidate's unknown-size
        # penalty — this is an actual file already on disk, not a search
        # result of uncertain provenance.
        size = f"{row.size_bytes / 1_048_576:.2f}MB"

        best_target = None
        best_score = 0.0
        for target in targets:
            cand = acquisition_service.AcquisitionResult(
                server=None,
                author=guess.author or "",
                title=guess.title,
                format="epub",
                size=size,
                full=row.path,
                provider="local",
            )
            score = acquisition_service.score_candidate(target["title"], target.get("author"), cand)
            if score > best_score:
                best_score, best_target = score, target

        if best_target is None or best_score < acquisition_service._MIN_SCORE:
            continue

        row.matched_request_id = best_target["request_id"]
        row.matched_title = best_target["title"]
        row.matched_author = best_target.get("author")
        row.matched_score = best_score

        if (
            automatch_on
            and row.status == LocalFileStatus.pending
            and best_score >= acquisition_service._STRONG_SCORE
        ):
            try:
                await inbox_upload_service.upload_local_file_to_inbox(
                    Path(row.path), row.filename, drive_provider, inbox_folder_id
                )
            except Exception:
                logger.exception("local_scan: auto-match upload failed for %s", row.filename)
                continue
            row.status = LocalFileStatus.copied
            if best_target["source"] == "wishlist":
                try:
                    # mark_wishlist_sourced is a plain sync function (blocking
                    # Drive I/O) — approve_request already runs it via
                    # asyncio.to_thread for the same reason; a bare `await`
                    # here would raise TypeError (a bool isn't awaitable),
                    # which the broad except below was silently swallowing
                    # every time this path ran.
                    await asyncio.to_thread(
                        acquisition_service.mark_wishlist_sourced,
                        drive_provider, library_folder_id, best_target["request_id"],
                    )
                except Exception:
                    logger.exception(
                        "local_scan: couldn't mark wishlist item %s sourced", best_target["request_id"]
                    )
            await _mark_candidate_resolved(session, best_target, row)

    await session.commit()


async def _mark_candidate_resolved(session: AsyncSession, target: dict, matched_row: LocalFile) -> None:
    """A strong-match auto-upload just happened for `target` — flip its
    AcquisitionCandidate row to approved so every acquisition cycle
    (OpenBooks/Libgen/torrent) stops treating it as still wanted, and so it
    counts correctly in the dashboard's provider breakdown. Runs for every
    target source (wishlist/want_to_read/list), not just wishlist — that
    scoping above is specifically for the wishlist-sidecar write, which is
    a different, narrower thing. Get-or-create: a want_to_read/list target
    may never have been searched before and so has no row yet."""
    row = (
        await session.execute(
            select(AcquisitionCandidate).where(AcquisitionCandidate.request_id == target["request_id"])
        )
    ).scalar_one_or_none()
    if row is None:
        row = AcquisitionCandidate(request_id=target["request_id"], request_title=target["title"])
        session.add(row)
    row.source = target.get("source") or "wishlist"
    row.request_title = target["title"]
    row.request_author = target.get("author")
    row.status = AcquisitionStatus.approved
    row.candidate_provider = "torrent"
    row.candidate_full = matched_row.path
    row.candidate_title = matched_row.matched_title or target["title"]
    row.candidate_author = matched_row.matched_author or target.get("author")
    row.candidate_server = None
    row.score = matched_row.matched_score
    row.message = None
    row.resolved_at = datetime.now(UTC)


async def dismiss(session: AsyncSession, file_ids: list[int]) -> int:
    count = 0
    for file_id in file_ids:
        row = await session.get(LocalFile, file_id)
        if row is None or row.status != LocalFileStatus.pending:
            continue
        row.status = LocalFileStatus.dismissed
        count += 1
    await session.commit()
    return count
