from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Author, Book, Identifier, IdentifierType, Series


async def resolve_book(
    session: AsyncSession,
    *,
    title: str,
    author: str | None,
    series: str | None,
    series_number: float | None,
    isbn13: str | None,
    isbn10: str | None,
) -> Book:
    author_row = await _find_or_create_author(session, author) if author else None
    series_row = await _find_or_create_series(session, series) if series else None

    query = select(Book).where(Book.canonical_title == title)
    query = (
        query.where(Book.author_id == author_row.id)
        if author_row is not None
        else query.where(Book.author_id.is_(None))
    )
    book_row = (await session.execute(query)).scalar_one_or_none()

    if book_row is None:
        book_row = Book(
            canonical_title=title,
            author_id=author_row.id if author_row else None,
            series_id=series_row.id if series_row else None,
            series_number=series_number,
        )
        session.add(book_row)
        await session.flush()

    if isbn13:
        await _ensure_identifier(session, book_row.id, IdentifierType.isbn13, isbn13)
    if isbn10:
        await _ensure_identifier(session, book_row.id, IdentifierType.isbn10, isbn10)

    return book_row


async def _find_or_create_author(session: AsyncSession, name: str) -> Author:
    row = (await session.execute(select(Author).where(Author.name == name))).scalar_one_or_none()
    if row is None:
        row = Author(name=name)
        session.add(row)
        await session.flush()
    return row


async def _find_or_create_series(session: AsyncSession, name: str) -> Series:
    row = (await session.execute(select(Series).where(Series.name == name))).scalar_one_or_none()
    if row is None:
        row = Series(name=name)
        session.add(row)
        await session.flush()
    return row


async def _ensure_identifier(
    session: AsyncSession, book_id: int, type_: IdentifierType, value: str
) -> None:
    existing = await session.execute(
        select(Identifier).where(
            Identifier.book_id == book_id,
            Identifier.type == type_,
            Identifier.value == value,
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(Identifier(book_id=book_id, type=type_, value=value, source="identification"))
