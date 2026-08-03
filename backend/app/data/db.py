from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """Each request opens its own connection/session. Dashboard polling
    (Book Dump, organize progress) now regularly overlaps with writes like
    review approval - the default rollback-journal mode serializes those
    with only sqlite3's default 5s busy timeout, so a slow writer can still
    make a concurrent reader (or another writer) fail outright instead of
    just waiting. WAL lets reads and writes proceed concurrently; the
    explicit busy_timeout is a backstop for the writer-vs-writer case WAL
    doesn't eliminate on its own. A no-op on :memory: test databases."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
