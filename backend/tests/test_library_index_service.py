from datetime import UTC, datetime

from sqlalchemy import select

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
import json

from app.services.library_index_service import (
    _plain_text,
    _read_pending_reading,
    build_index_payload,
    build_new_releases_payload,
    build_reading_payload,
    build_recommendations_payload,
)


class _FakeProvider:
    """Minimal stand-in: one folder, files addressed by name."""

    def __init__(self, files: dict[str, bytes]):
        self._by_name = files
        self._ids = {name: f"id-{name}" for name in files}

    def list_files_in_folder(self, _folder_id: str):
        return [{"id": self._ids[n], "name": n} for n in self._by_name]

    def download_file(self, file_id: str) -> bytes:
        name = next(n for n, i in self._ids.items() if i == file_id)
        return self._by_name[name]


def test_read_pending_reading_filters_to_valid_changes() -> None:
    raw = {
        "version": 1,
        "changes": [
            {"driveFileId": "d1", "status": "read", "title": "A"},
            {"status": "read"},  # no driveFileId → dropped
            "nonsense",  # not a dict → dropped
        ],
    }
    provider = _FakeProvider({"bookbrain-reading-pending.json": json.dumps(raw).encode()})
    out = _read_pending_reading(provider, "folder")
    assert [c["driveFileId"] for c in out] == ["d1"]


def test_read_pending_reading_missing_file_is_empty() -> None:
    assert _read_pending_reading(_FakeProvider({}), "folder") == []


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

    assert payload["version"] == 5
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

    # No Hardcover data seeded → empty series map.
    assert payload["series"] == {}


async def test_build_index_payload_includes_matched_hardcover_series(db_session) -> None:
    await _seed(db_session)
    series = (
        await db_session.execute(select(Series).where(Series.name == "The Hierarchy"))
    ).scalar_one()
    series.hardcover_json = {
        "id": 55,
        "name": "The Hierarchy",
        "slug": "the-hierarchy",
        "primaryCount": 3,
        "books": [
            {
                "position": 1.0,
                "title": "The Will of the Many",
                "releaseDate": "2023-05-23",
                "isbn13": "9781234567890",
            },
            {"position": 2.0, "title": "The Strength of the Few"},
        ],
        "match": "auto",
    }
    # An unmatched series must not appear.
    other = Series(name="Ghostwater", hardcover_json={"match": "none"})
    db_session.add(other)
    await db_session.commit()

    payload = await build_index_payload(db_session)

    assert set(payload["series"]) == {"The Hierarchy"}
    entry = payload["series"]["The Hierarchy"]
    assert entry["hardcoverSlug"] == "the-hierarchy"
    assert entry["primaryCount"] == 3
    assert [b["title"] for b in entry["books"]] == [
        "The Will of the Many",
        "The Strength of the Few",
    ]
    assert entry["books"][0]["releaseDate"] == "2023-05-23"  # prompts/26 Part A
    assert entry["books"][0]["isbn13"] == "9781234567890"  # prompts/27 Part 1


async def test_build_index_payload_includes_hardcover_meta(db_session) -> None:
    await _seed(db_session)
    will = (
        await db_session.execute(select(Book).where(Book.canonical_title == "The Will of the Many"))
    ).scalar_one()
    will.hardcover_json = {
        "id": 1,
        "similar": [],
        "meta": {
            "rating": 4.42,
            "ratingsCount": 5645,
            "pages": 541,
            "category": "Book",
            "literaryType": "Fiction",
            "genres": ["Fantasy", "Epic Fantasy"],
            "moods": ["dark", "tense"],
            "description": "A boy hides who he is.",  # not carried into the index
        },
    }
    scion = (
        await db_session.execute(select(Book).where(Book.canonical_title == "Scion"))
    ).scalar_one()
    scion.hardcover_json = {"similar": [], "meta": {}}  # looked, found nothing
    await db_session.commit()

    payload = await build_index_payload(db_session)

    meta = payload["books"]["drive-will"]["meta"]
    assert meta == {
        "rating": 4.42,
        "ratingsCount": 5645,
        "pages": 541,
        "category": "Book",
        "literaryType": "Fiction",
        "genres": ["Fantasy", "Epic Fantasy"],
        "moods": ["dark", "tense"],
    }
    assert "meta" not in payload["books"]["drive-scion"]  # empty meta omitted


async def test_build_recommendations_payload(db_session) -> None:
    await _seed(db_session)
    will = (
        await db_session.execute(select(Book).where(Book.canonical_title == "The Will of the Many"))
    ).scalar_one()
    will.hardcover_json = {
        "id": 1,
        "similar": [
            {"title": "The Will of the Many", "author": "James Islington", "isbn13": None},  # self
            {"title": "Red Rising", "author": "Pierce Brown", "isbn13": "9780553588484"},
            {"title": "The Poppy War", "author": "R.F. Kuang", "isbn13": None},
        ],
    }
    await db_session.commit()

    payload = await build_recommendations_payload(db_session)

    assert payload["version"] == 1
    # self-reference dropped, only the file that has recs is present
    assert list(payload["books"]) == ["drive-will"]
    assert [r["title"] for r in payload["books"]["drive-will"]] == ["Red Rising", "The Poppy War"]


async def test_build_new_releases_payload_excludes_owned_and_wishlisted(db_session) -> None:
    await _seed(db_session)  # James Islington owns "The Will of the Many" + "Scion"
    author = (
        await db_session.execute(select(Author).where(Author.name == "James Islington"))
    ).scalar_one()
    today = datetime.now(UTC).date()
    author.hardcover_json = {
        "books": [
            {"title": "The Will of the Many", "releaseDate": "2023-05-23"},  # owned → drop
            {"title": "The Strength of the Few", "releaseDate": "2020-01-01"},  # wishlisted → drop
            {"title": "Blade Breaker", "releaseDate": "2024-01-01", "isbn13": "9990000000001"},
            {
                "title": "The Hierarchy 3",
                "releaseDate": (today.replace(year=today.year + 1)).isoformat(),
                "genres": ["Fantasy"],
            },
        ]
    }
    await db_session.commit()

    from app.services.library_index_service import _norm_key

    wishlist_keys = {_norm_key("The Strength of the Few", "James Islington")}
    global_raw = [
        {"title": "Blade Breaker", "author": "James Islington", "releaseDate": "2024-01-01"},  # dup
        {"title": "Some Hyped Book", "author": "Other Person", "releaseDate": "2027-01-01"},
    ]
    payload = await build_new_releases_payload(db_session, wishlist_keys, global_raw)

    assert payload["version"] == 1
    assert [b["title"] for b in payload["recent"]] == ["Blade Breaker"]
    assert [b["title"] for b in payload["upcoming"]] == ["The Hierarchy 3"]
    assert payload["recent"][0]["source"] == "author"
    assert payload["upcoming"][0]["genres"] == ["Fantasy"]
    # global: the author-feed dup is dropped, the fresh one kept
    assert [b["title"] for b in payload["global"]] == ["Some Hyped Book"]
    assert payload["global"][0]["source"] == "global"


async def test_build_reading_payload_matches_and_counts_unmatched(db_session) -> None:
    await _seed(db_session)  # drive-will (isbn13 9781234567890), drive-scion (title/author)
    rows = [
        {"title": "The Will of the Many", "author": "?", "isbn13": "9781234567890",
         "status": "read", "rating": 4.5, "readDate": "2026-01-02", "readCount": 1},
        {"title": "Scion", "author": "James Islington", "isbn13": None,
         "status": "reading", "rating": None, "readDate": None, "readCount": 0},
        {"title": "A Book Not In The Library", "author": "Someone", "isbn13": "9990000000000",
         "status": "read", "rating": 5.0, "readDate": None, "readCount": 1},
    ]
    payload = await build_reading_payload(db_session, rows, "James")

    assert payload["version"] == 1 and payload["reader"] == "James" and payload["count"] == 2
    assert payload["books"]["drive-will"] == {
        "status": "read", "rating": 4.5, "readDate": "2026-01-02", "readCount": 1
    }
    assert payload["books"]["drive-scion"] == {"status": "reading"}  # no rating/date/count keys
    assert payload["unmatched"] == {"read": 1, "want": 0, "reading": 0}
