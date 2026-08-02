from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    File,
    Identifier,
    LibraryRule,
    MetadataSource,
    Operation,
    Review,
    Series,
    SeriesAlias,
)

# Children before parents, per the FK graph — everything the scanner/
# identifier/organizer produces, but never `Setting` (Drive connection,
# folder choice, dry-run mode survive a clear so you don't have to
# reconnect and re-pick folders just to retest the pipeline).
_TABLES_IN_DELETE_ORDER = [
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
