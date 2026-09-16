"""The wishlist: books the user wants but doesn't own. `resolve_request`
turns a rough free-text description into a specific book (Claude, then
Google Books / Open Library to firm up title/author/ISBN), and checks it
isn't already in the library or on the list. `reconcile` flips items to
`acquired` once a matching book shows up in the library."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    WishlistItem,
    WishlistStatus,
)
from app.providers.ai.anthropic_client import AnthropicIdentificationClient
from app.schemas.wishlist import (
    LibraryMatch,
    ResolvedBook,
    ResolveResult,
    WishlistItemCreate,
)
from app.services.candidate_service import default_candidate_service
from app.services.text_match import normalize_title, normalize_words

logger = logging.getLogger(__name__)

_LIVE_STATUSES = (FileStatus.organised, FileStatus.inbox, FileStatus.review)


def _cover_url(isbn13: str | None) -> str | None:
    return f"https://covers.openlibrary.org/b/isbn/{isbn13}-M.jpg" if isbn13 else None


async def _library_match(
    session: AsyncSession, *, isbn13: str | None, title: str, author: str | None
) -> File | None:
    """A file in the library that's the same book — ISBN first, then a
    fuzzy title (+ author) match, mirroring book_repository's matching."""
    if isbn13:
        row = (
            await session.execute(
                select(File)
                .join(Identifier, Identifier.book_id == File.book_id)
                .where(
                    Identifier.type == IdentifierType.isbn13,
                    Identifier.value == isbn13,
                    File.status.in_(_LIVE_STATUSES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            return row

    target_title = normalize_title(title)
    target_author = normalize_words(author) if author else None
    rows = (
        await session.execute(
            select(File, Book, Author)
            .join(Book, File.book_id == Book.id)
            .outerjoin(Author, Book.author_id == Author.id)
            .where(File.status.in_(_LIVE_STATUSES))
        )
    ).all()
    for file_row, book, book_author in rows:
        if normalize_title(book.canonical_title) != target_title:
            continue
        if target_author is not None:
            if book_author is None or normalize_words(book_author.name) != target_author:
                continue
        return file_row
    return None


async def _wishlist_match(
    session: AsyncSession, *, title: str, author: str | None
) -> WishlistItem | None:
    target_title = normalize_title(title)
    target_author = normalize_words(author) if author else None
    for item in (await session.execute(select(WishlistItem))).scalars():
        if normalize_title(item.title) != target_title:
            continue
        if target_author is not None and (
            not item.author or normalize_words(item.author) != target_author
        ):
            continue
        return item
    return None


async def resolve_request(session: AsyncSession, text: str) -> ResolveResult:
    text = text.strip()
    if not text:
        return ResolveResult(found=False, note="Nothing to look up.")

    guess = await AnthropicIdentificationClient().resolve_book_request(text)
    if not guess.found or not guess.title:
        return ResolveResult(found=False, note=guess.note or "Couldn't identify a specific book.")

    title, author = guess.title, guess.author
    isbn13 = guess.isbn13
    note = guess.note

    # Firm up title / author / ISBN against Google Books + Open Library.
    try:
        candidates = await default_candidate_service().generate_candidates(
            isbn13=isbn13, title=title, authors=author
        )
    except Exception:
        logger.exception("wishlist resolve: candidate lookup failed")
        candidates = []
    if candidates:
        best = candidates[0]
        if best.title:
            title = best.title
        if best.authors:
            author = best.authors[0]
        isbn13 = isbn13 or best.isbn13

    resolved = ResolvedBook(
        title=title,
        author=author,
        series=guess.series,
        series_number=guess.series_number,
        isbn13=isbn13,
        cover_url=_cover_url(isbn13),
        note=note,
    )

    in_library = await _library_match(session, isbn13=isbn13, title=title, author=author)
    on_wishlist = await _wishlist_match(session, title=title, author=author)

    return ResolveResult(
        found=True,
        resolved=resolved,
        already_in_library=(
            LibraryMatch(
                file_id=in_library.id, filename=in_library.filename, status=in_library.status.value
            )
            if in_library
            else None
        ),
        already_on_wishlist=on_wishlist is not None,
        note=note,
    )


async def add_item(session: AsyncSession, body: WishlistItemCreate) -> WishlistItem:
    existing = await _wishlist_match(session, title=body.title, author=body.author)
    if existing is not None:
        return existing

    item = WishlistItem(
        raw_request=body.raw_request,
        title=body.title,
        author=body.author,
        series=body.series,
        series_number=body.series_number,
        isbn13=body.isbn13,
        cover_url=body.cover_url or _cover_url(body.isbn13),
        note=body.note,
        status=WishlistStatus.wanted,
    )
    session.add(item)
    await session.flush()
    await _reconcile_item(session, item)
    await session.commit()
    await session.refresh(item)
    return item


async def list_items(session: AsyncSession) -> list[WishlistItem]:
    changed = await reconcile(session)
    if changed:
        await session.commit()
    return list(
        (
            await session.execute(select(WishlistItem).order_by(WishlistItem.created_at.desc()))
        ).scalars()
    )


async def set_status(session: AsyncSession, item_id: int, status: str) -> WishlistItem:
    item = await session.get(WishlistItem, item_id)
    if item is None:
        raise WishlistItemNotFound(f"wishlist item {item_id} not found")
    item.status = WishlistStatus(status)
    if item.status == WishlistStatus.acquired and item.acquired_at is None:
        item.acquired_at = datetime.now(UTC)
    if item.status == WishlistStatus.wanted:
        item.acquired_at = None
        item.acquired_file_id = None
    await session.commit()
    await session.refresh(item)
    return item


async def delete_item(session: AsyncSession, item_id: int) -> None:
    item = await session.get(WishlistItem, item_id)
    if item is None:
        raise WishlistItemNotFound(f"wishlist item {item_id} not found")
    await session.delete(item)
    await session.commit()


async def reconcile(session: AsyncSession) -> int:
    """Flip every still-wanted item that's now in the library to acquired.
    Pure DB — safe to call often (list_items does)."""
    wanted = (
        await session.execute(
            select(WishlistItem).where(WishlistItem.status == WishlistStatus.wanted)
        )
    ).scalars()
    count = 0
    for item in wanted:
        if await _reconcile_item(session, item):
            count += 1
    return count


async def _reconcile_item(session: AsyncSession, item: WishlistItem) -> bool:
    if item.status != WishlistStatus.wanted:
        return False
    match = await _library_match(
        session, isbn13=item.isbn13, title=item.title, author=item.author
    )
    if match is None:
        return False
    item.status = WishlistStatus.acquired
    item.acquired_at = datetime.now(UTC)
    item.acquired_file_id = match.id
    return True


class WishlistItemNotFound(Exception):
    pass
