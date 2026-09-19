import csv
import io
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    DismissedReidentFlag,
    File,
    Identifier,
    LibraryRule,
    MetadataSource,
    Operation,
    Review,
    Series,
    SeriesAlias,
)
from app.providers.drive.provider import DriveProvider
from app.schemas.library import LibraryExportResult
from app.services import file_service

# Children before parents, per the FK graph — everything the scanner/
# identifier/organizer produces, but never `Setting` (Drive connection,
# folder choice, dry-run mode survive a clear so you don't have to
# reconnect and re-pick folders just to retest the pipeline).
_TABLES_IN_DELETE_ORDER = [
    # book-id-keyed, no FK — a stale flag would wrongly suppress a re-scanned
    # book that happens to reuse the id, so it goes when the library does.
    DismissedReidentFlag,
    LibraryRule,
    Review,
    Operation,
    AIDecision,
    BookCandidate,
    MetadataSource,
    File,
    Identifier,
    Book,
    SeriesAlias,
    Series,
    Author,
]


async def clear_library(session: AsyncSession) -> None:
    for model in _TABLES_IN_DELETE_ORDER:
        await session.execute(delete(model))
    await session.commit()


_EXPORT_HEADERS = [
    "Title",
    "Author",
    "Series",
    "Series #",
    "Status",
    "Filename",
    "Quality score",
    "Discovered",
]


async def export_to_sheet(
    session: AsyncSession, provider: DriveProvider, *, parent_id: str | None
) -> LibraryExportResult:
    """Every tracked file (whatever its status) as a real Google Sheet, not
    a downloaded CSV — dropped straight into the library folder so it's
    somewhere you'd actually stumble back onto it later."""
    files = await file_service.list_files(session)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_EXPORT_HEADERS)
    for f in files:
        writer.writerow(
            [
                f.book_title or "",
                f.book_author or "",
                f.book_series or "",
                f.book_series_number if f.book_series_number is not None else "",
                f.status,
                f.filename,
                f.quality_score if f.quality_score is not None else "",
                f.discovered_at,
            ]
        )

    name = f"BookBrain Library — {datetime.now():%Y-%m-%d}"
    result = provider.create_spreadsheet_from_csv(
        name=name, csv_bytes=buffer.getvalue().encode("utf-8"), parent_id=parent_id
    )
    return LibraryExportResult(name=result["name"], url=result["webViewLink"])
