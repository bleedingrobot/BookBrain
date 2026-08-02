from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import AIDecision, BookCandidate, File, FileStatus, MetadataSource, Operation, Review
from app.providers.drive.provider import DriveProvider
from app.schemas.duplicates import ClearDuplicatesResult, DuplicateGroup


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
