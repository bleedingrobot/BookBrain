from sqlalchemy import select

from app.data.models import Author, Book, File, FileStatus, FileStatusReason, MetadataSource, Series
from app.services.title_merge_repair_service import repair_title_merges


async def _seed_merged_pair(db_session) -> tuple[Book, File, File]:
    author = Author(name="Brandon Sanderson")
    series = Series(name="Mistborn")
    db_session.add_all([author, series])
    await db_session.flush()
    book = Book(
        canonical_title="Mistborn: The Final Empire",
        author_id=author.id,
        series_id=series.id,
        series_number=1.0,
    )
    db_session.add(book)
    await db_session.flush()

    primary = File(
        drive_file_id="primary",
        drive_parent_id="p",
        filename="final-empire.epub",
        sha256="a",
        size_bytes=100,
        status=FileStatus.organised,
        book_id=book.id,
        quality_score=90,
    )
    casualty = File(
        drive_file_id="casualty",
        drive_parent_id="p",
        filename="well-of-ascension.epub",
        sha256="b",
        size_bytes=100,
        status=FileStatus.duplicate,
        status_reason=FileStatusReason.same_book,
        book_id=book.id,
        quality_score=90,
    )
    db_session.add_all([primary, casualty])
    await db_session.flush()
    db_session.add_all(
        [
            MetadataSource(
                file_id=primary.id, field_name="title",
                value="Mistborn: The Final Empire", source="epub",
            ),
            MetadataSource(
                file_id=casualty.id, field_name="title",
                value="Mistborn: The Well of Ascension", source="epub",
            ),
        ]
    )
    await db_session.commit()
    return book, primary, casualty


async def test_repair_splits_a_falsely_merged_book(db_session) -> None:
    book, primary, casualty = await _seed_merged_pair(db_session)

    result = await repair_title_merges(db_session)

    assert result.books_split == 1
    assert result.files_moved == 1

    await db_session.refresh(primary)
    await db_session.refresh(casualty)
    assert primary.book_id == book.id
    assert casualty.book_id != book.id
    assert casualty.status == FileStatus.inbox
    assert casualty.status_reason is None

    new_book = (
        await db_session.execute(select(Book).where(Book.id == casualty.book_id))
    ).scalar_one()
    assert new_book.canonical_title == "Mistborn: The Well of Ascension"
    assert new_book.author_id == book.author_id
    assert new_book.series_id == book.series_id


async def test_repair_is_a_noop_on_a_correctly_grouped_book(db_session) -> None:
    author = Author(name="Some Author")
    db_session.add(author)
    await db_session.flush()
    book = Book(canonical_title="A Real Book", author_id=author.id)
    db_session.add(book)
    await db_session.flush()
    for i in range(2):
        f = File(
            drive_file_id=f"f{i}",
            drive_parent_id="p",
            filename=f"f{i}.epub",
            sha256=f"h{i}",
            size_bytes=100,
            status=FileStatus.organised,
            book_id=book.id,
        )
        db_session.add(f)
        await db_session.flush()
        db_session.add(
            MetadataSource(file_id=f.id, field_name="title", value="A Real Book", source="epub")
        )
    await db_session.commit()

    result = await repair_title_merges(db_session)

    assert result.books_split == 0
    assert result.files_moved == 0
    assert len((await db_session.execute(select(Book))).scalars().all()) == 1


async def test_repair_is_idempotent(db_session) -> None:
    await _seed_merged_pair(db_session)

    first = await repair_title_merges(db_session)
    second = await repair_title_merges(db_session)

    assert first.files_moved == 1
    assert second.files_moved == 0
