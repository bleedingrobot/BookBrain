from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import File, FileStatus
from app.schemas.duplicates import DuplicateGroup


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
                sha256=dup.sha256,
            )
        )
    return groups
