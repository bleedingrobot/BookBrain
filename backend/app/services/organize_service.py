import re
import uuid

from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.db import async_session_factory
from app.data.models import Book, File, FileStatus, Operation, OperationAction, OperationStatus
from app.providers.drive.client import build_drive_service
from app.providers.drive.provider import DriveProvider
from app.schemas.organize import OrganizeJobState, OrganizeJobStatus

_INVALID_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize(segment: str) -> str:
    cleaned = _INVALID_CHARS_RE.sub(" ", segment).strip()
    return cleaned or "Untitled"


def build_target_path(
    *, title: str, author_name: str | None, series_name: str | None, series_number: float | None
) -> tuple[list[str], str]:
    """Returns (folder_segments, filename). `folder_segments` is relative to
    the configured library root — e.g. ["Frank Herbert", "Dune Chronicles"]."""
    folders: list[str] = []
    if author_name:
        folders.append(_sanitize(author_name))
    if series_name:
        folders.append(_sanitize(series_name))

    if series_name and series_number is not None:
        filename = f"{series_number:g} - {_sanitize(title)}.epub"
    else:
        filename = f"{_sanitize(title)}.epub"

    return folders, filename


def _ensure_folder_path(provider: DriveProvider, root_id: str, segments: list[str]) -> str:
    current = root_id
    for segment in segments:
        match = next((f for f in provider.list_folders(current) if f["name"] == segment), None)
        current = match["id"] if match else provider.create_folder(segment, parent_id=current)["id"]
    return current


class OrganizeService:
    """SPEC.md's Milestone 6/6a: builds move/rename/logging, wired only to a
    dry-run flag. Dry runs never touch Drive — the target path is logged as
    a display string, not a real folder ID, since no folder is created to
    resolve one. Only a real (non-dry-run) run creates folders, moves the
    file, and flips `files.status` to `organised`."""

    def __init__(self) -> None:
        self._jobs: dict[str, OrganizeJobStatus] = {}

    def create_job(self) -> OrganizeJobStatus:
        job_id = str(uuid.uuid4())
        status = OrganizeJobStatus(job_id=job_id, status=OrganizeJobState.running)
        self._jobs[job_id] = status
        return status

    def get_status(self, job_id: str) -> OrganizeJobStatus | None:
        return self._jobs.get(job_id)

    async def run_organize(
        self,
        job_id: str,
        creds: Credentials | None,
        library_root_folder_id: str | None,
        dry_run: bool,
    ) -> None:
        counts = {"organized": 0, "failed": 0}
        provider = DriveProvider(build_drive_service(creds)) if not dry_run and creds else None

        async with async_session_factory() as session:
            result = await session.execute(
                select(File)
                .where(File.status == FileStatus.inbox, File.book_id.is_not(None))
                .options(selectinload(File.book).selectinload(Book.author))
                .options(selectinload(File.book).selectinload(Book.series))
            )
            for file_row in result.scalars().all():
                try:
                    await self._organize_file(
                        session,
                        file_row,
                        provider=provider,
                        library_root_folder_id=library_root_folder_id,
                        dry_run=dry_run,
                    )
                    counts["organized"] += 1
                except Exception:
                    counts["failed"] += 1

        detail = (
            f"{counts['organized']} organized"
            f"{' (dry run — nothing changed in Drive)' if dry_run else ''}, "
            f"{counts['failed']} failed"
        )
        self._jobs[job_id] = OrganizeJobStatus(job_id=job_id, status=OrganizeJobState.done, detail=detail)

    async def _organize_file(
        self,
        session: AsyncSession,
        file_row: File,
        *,
        provider: DriveProvider | None,
        library_root_folder_id: str | None,
        dry_run: bool,
    ) -> Operation:
        book = file_row.book
        author_name = book.author.name if book.author else None
        series_name = book.series.name if book.series else None
        folders, filename = build_target_path(
            title=book.canonical_title,
            author_name=author_name,
            series_name=series_name,
            series_number=book.series_number,
        )

        if dry_run:
            operation = Operation(
                file_id=file_row.id,
                action=OperationAction.move_and_rename,
                original_name=file_row.filename,
                original_parent_id=file_row.drive_parent_id,
                new_name=filename,
                new_parent_id="/".join(folders) or "(library root)",
                status=OperationStatus.done,
                dry_run=True,
                reason="dry run — no Drive changes made",
            )
            session.add(operation)
            await session.commit()
            return operation

        if provider is None or library_root_folder_id is None:
            raise RuntimeError("organize requires Drive credentials and a configured library folder")

        target_folder_id = _ensure_folder_path(provider, library_root_folder_id, folders)
        provider.move_and_rename(
            file_row.drive_file_id,
            old_parent_id=file_row.drive_parent_id,
            new_parent_id=target_folder_id,
            new_name=filename,
        )

        operation = Operation(
            file_id=file_row.id,
            action=OperationAction.move_and_rename,
            original_name=file_row.filename,
            original_parent_id=file_row.drive_parent_id,
            new_name=filename,
            new_parent_id=target_folder_id,
            status=OperationStatus.done,
            dry_run=False,
        )
        session.add(operation)

        file_row.filename = filename
        file_row.drive_parent_id = target_folder_id
        file_row.status = FileStatus.organised

        await session.commit()
        return operation


_organize_service = OrganizeService()


def get_organize_service() -> OrganizeService:
    return _organize_service
