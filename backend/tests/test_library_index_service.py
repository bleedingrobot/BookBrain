from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    MetadataSource,
    Series,
)
from app.services.library_index_service import _plain_text, build_index_payload


def test_plain_text_strips_html_and_caps() -> None:
    assert _plain_text("<b>John Wick</b><BR>meets <i>Ghost</i>") == "John Wick meets Ghost"
    assert _plain_text(None) is None
    assert _plain_text("   <br>  ") is None
    assert len(_plain_text("x" * 5000)) == 1500


def test_plain_text_strips_publisher_labels() -> None:
    assert _plain_text("SUMMARY: There are no rules in the dark.") == "There are no rules in the dark."
    assert _plain_text("<p>Publisher's Description:</p> A boy.") == "A boy."
    assert _plain_text("From the Publisher — Great book") == "Great book"
    # only a leading label, and only once
    assert _plain_text("A summary: of events") == "A summary: of events"


async def _seed(db_session) -> None:
    author = Author(name="James Islington")
    series = Series(name="The Hierarchy")
    db_session.add_all([author, series])
    await db_session.flush()

    organised = Book(
        canonical_title="The Will of the Many",
        author_id=author.id,
        series_id=series.id,
        series_number=1,
        description="A boy hides who he is.",
    )
    standalone = Book(canonical_title="Scion", author_id=author.id)
    inbox_book = Book(canonical_title="Not Placed Yet", author_id=author.id)
    db_session.add_all([organised, standalone, inbox_book])
    await db_session.flush()

    placed = File(
        drive_file_id="drive-will",
        filename="James Islington, The Will of the Many, The Hierarchy, 1.epub",
        sha256="a",
        size_bytes=1,
        status=FileStatus.organised,
        book_id=organised.id,
    )
    placed_2 = File(
        drive_file_id="drive-scion",
        filename="James Islington, Scion.epub",
        sha256="b",
        size_bytes=1,
        status=FileStatus.organised,
        book_id=standalone.id,
    )
    not_placed = File(
        drive_file_id="drive-inbox",
        filename="whatever.epub",
        sha256="c",
        size_bytes=1,
        status=FileStatus.inbox,
        book_id=inbox_book.id,
    )
    db_session.add_all([placed, placed_2, not_placed])
    await db_session.flush()

    # Scion has no book.description — the EPUB blurb should be used instead.
    db_session.add(
        MetadataSource(
            file_id=placed_2.id,
            field_name="description",
            value="<b>John Wick meets Ghost in the Shell.</b>",
            source="epub",
        )
    )
    db_session.add(Identifier(book_id=organised.id, type=IdentifierType.isbn13, value="9781234567890"))
    db_session.add(Identifier(book_id=standalone.id, type=IdentifierType.isbn10, value="1668239248"))
    await db_session.commit()


async def test_build_index_payload_only_organised_files(db_session) -> None:
    await _seed(db_session)
    payload = await build_index_payload(db_session)

    assert payload["version"] == 2
    assert payload["count"] == 2
    assert set(payload["books"]) == {"drive-will", "drive-scion"}

    will = payload["books"]["drive-will"]
    assert will["title"] == "The Will of the Many"
    assert will["author"] == "James Islington"
    assert will["series"] == "The Hierarchy"
    assert will["seriesNumber"] == 1
    assert will["description"] == "A boy hides who he is."
    assert will["isbn"] == "9781234567890"

    scion = payload["books"]["drive-scion"]
    assert scion["series"] is None
    assert scion["seriesNumber"] is None
    assert scion["description"] == "John Wick meets Ghost in the Shell."
    assert scion["isbn"] == "1668239248"
