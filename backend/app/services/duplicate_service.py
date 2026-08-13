from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    AIDecision,
    BookCandidate,
    File,
    FileStatus,
    FileStatusReason,
    MetadataSource,
    Operation,
    Review,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup

_ACTIVE_STATUSES = (FileStatus.inbox, FileStatus.review, FileStatus.organised)


async def detect_same_book_duplicates(session: AsyncSession) -> int:
    """Flags every file beyond the best copy of the same book as a
    duplicate. sha256-based detection (the scan-time check) only catches
    byte-identical re-uploads; a different edition or re-conversion of the
    same book has different bytes entirely and sails right past it, while
    still cluttering the same Drive folder with 2-3 copies of one title.
    Primary is chosen by quality_score, then the oldest (first discovered)
    file wins a tie — assumed to have been trusted/organized longest."""
    files = (
        (
            await session.execute(
                select(File).where(File.book_id.is_not(None), File.status.in_(_ACTIVE_STATUSES))
            )
        )
        .scalars()
        .all()
    )

    groups: dict[int, list[File]] = defaultdict(list)
    for f in files:
        groups[f.book_id].append(f)

    flagged = 0
    for group in groups.values():
        if len(group) <= 1:
            continue
        group.sort(key=lambda f: (-(f.quality_score or 0), f.discovered_at, f.id))
        for extra in group[1:]:
            extra.status = FileStatus.duplicate
            extra.status_reason = FileStatusReason.same_book
            flagged += 1

    return flagged


async def list_duplicate_groups(session: AsyncSession) -> list[DuplicateGroup]:
    duplicates = (
        (await session.execute(select(File).where(File.status == FileStatus.duplicate)))
        .scalars()
        .all()
    )

    groups: list[DuplicateGroup] = []
    for dup in duplicates:
        primary = (
            (
                await session.execute(
                    select(File).where(File.sha256 == dup.sha256, File.status != FileStatus.duplicate)
                )
            )
            .scalars()
            .first()
        )
        # sha256 differs for a same_book duplicate by definition (different
        # bytes, same book) — the exact-content lookup above can't find it,
        # so fall back to the book it was actually flagged against.
        if primary is None and dup.book_id is not None:
            primary = (
                (
                    await session.execute(
                        select(File).where(
                            File.book_id == dup.book_id, File.status != FileStatus.duplicate
                        )
                    )
                )
                .scalars()
                .first()
            )
        groups.append(
            DuplicateGroup(
                duplicate_file_id=dup.id,
                duplicate_filename=dup.filename,
                quality_score=dup.quality_score,
                primary_file_id=primary.id if primary else None,
                primary_filename=primary.filename if primary else None,
                status_reason=dup.status_reason.value if dup.status_reason else None,
                sha256=dup.sha256,
            )
        )
    return groups


async def clear_duplicates(session: AsyncSession, provider: DriveProvider) -> ClearDuplicatesResult:
    """Trashes each duplicate's Drive file (recoverable via Drive's own
    Trash, not a permanent delete) and drops its DB record. Never touches
    the primary — only rows with status=duplicate are ever candidates."""
    duplicates = (
        (await session.execute(select(File).where(File.status == FileStatus.duplicate)))
        .scalars()
        .all()
    )

    cleared = 0
    failed = 0
    for dup in duplicates:
        try:
            provider.trash_file(dup.drive_file_id)
        except Exception:
            failed += 1
            continue

        for model in (Review, Operation, AIDecision, BookCandidate, MetadataSource):
            await session.execute(delete(model).where(model.file_id == dup.id))
        await session.execute(delete(File).where(File.id == dup.id))
        cleared += 1

    await session.commit()
    return ClearDuplicatesResult(cleared=cleared, failed=failed)
