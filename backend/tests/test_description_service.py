from app.data.models import Author, Book, File, FileStatus, MetadataSource
from app.services.description_service import _books_needing_descriptions, _clean


def test_clean_strips_html_and_whitespace() -> None:
    assert _clean("<p>Hello   <b>world</b></p>\n\n") == "Hello world"
    assert _clean(None) is None
    assert _clean("  <br>  ") is None


async def _seed(db_session) -> None:
    author = Author(name="A")
    db_session.add(author)
    await db_session.flush()

    needs = Book(canonical_title="Needs one", author_id=author.id)
    has_own = Book(canonical_title="Has its own", author_id=author.id, description="already here")
    has_epub = Book(canonical_title="Has an epub blurb", author_id=author.id)
    not_organised = Book(canonical_title="Still in inbox", author_id=author.id)
    db_session.add_all([needs, has_own, has_epub, not_organised])
    await db_session.flush()

    def f(book: Book, status: FileStatus) -> File:
        return File(
            drive_file_id=f"d{book.id}",
            filename=f"{book.canonical_title}.epub",
            sha256=f"s{book.id}",
            size_bytes=1,
            status=status,
            book_id=book.id,
        )

    files = [
        f(needs, FileStatus.organised),
        f(has_own, FileStatus.organised),
        f(has_epub, FileStatus.organised),
        f(not_organised, FileStatus.inbox),
    ]
    db_session.add_all(files)
    await db_session.flush()

    epub_file = next(x for x in files if x.book_id == has_epub.id)
    db_session.add(
        MetadataSource(
            file_id=epub_file.id, field_name="description", value="from the epub", source="epub"
        )
    )
    await db_session.commit()


async def test_books_needing_descriptions_only_returns_the_genuine_blanks(db_session) -> None:
    await _seed(db_session)
    rows = await _books_needing_descriptions(db_session)
    titles = {title for _, title, _ in rows}
    assert titles == {"Needs one"}
    assert rows[0][2] == "A"  # author name comes through
