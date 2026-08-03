import mimetypes
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import LocalFile, LocalFileStatus
from app.providers.convert.calibre import is_convertible
from app.providers.drive.classify import is_supported_ebook
from app.providers.drive.provider import DriveProvider


def _is_watchable(filename: str) -> bool:
    return is_supported_ebook(filename) or is_convertible(filename)


async def scan_local_folder(session: AsyncSession, root: str) -> list[LocalFile]:
    """Walks `root` recursively, recording every not-yet-seen .epub/.kpub/
    .mobi/.rtf file as a pending LocalFile row (keyed by absolute path, so a
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
            data = Path(row.path).read_bytes()
            mime_type = mimetypes.guess_type(row.filename)[0] or "application/octet-stream"
            provider.upload_new_file(name=row.filename, data=data, parent_id=inbox_folder_id, mime_type=mime_type)
        except Exception:
            failed += 1
            continue
        row.status = LocalFileStatus.copied
        copied += 1
    await session.commit()
    return {"copied": copied, "failed": failed}


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
