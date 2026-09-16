from sqlalchemy import select

from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    LibraryRule,
    MetadataSource,
    Operation,
    OperationAction,
    Review,
    ReviewStatus,
    RuleType,
    Series,
    SeriesAlias,
)
from app.data.repositories.settings_repository import SettingsRepository
from app.services import library_service


async def _seed_full_graph(db_session) -> None:
    author = Author(name="Frank Herbert")
    series = Series(name="Dune Chronicles")
    db_session.add_all([author, series])
    await db_session.flush()

    db_session.add(SeriesAlias(series_id=series.id, alias="Dune Saga"))

    book = Book(canonical_title="Dune", author_id=author.id, series_id=series.id, series_number=1)
    db_session.add(book)
    await db_session.flush()

    db_session.add(Identifier(book_id=book.id, type=IdentifierType.isbn13, value="9780441172719"))

    file_row = File(
        drive_file_id="drive-1",
        drive_parent_id="p",
        filename="dune.epub",
        sha256="abc123",
        size_bytes=100,
        status=FileStatus.review,
        book_id=book.id,
    )
    db_session.add(file_row)
    await db_session.flush()

    db_session.add(MetadataSource(file_id=file_row.id, field_name="title", value="Dune", source="epub"))
    db_session.add(BookCandidate(file_id=file_row.id, title="Dune", source="a"))
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model="fake",
            prompt_hash="h",
            evidence_hash="h",
            raw_response_json={},
            computed_confidence=80,
        )
    )
    db_session.add(
        Operation(
            file_id=file_row.id,
            action=OperationAction.move_and_rename,
            new_name="Dune.epub",
            dry_run=True,
        )
    )
    review = Review(file_id=file_row.id, status=ReviewStatus.pending, proposed_json={})
    db_session.add(review)
    await db_session.flush()

    db_session.add(
        LibraryRule(
            rule_type=RuleType.author_alias,
            pattern="Frank Herbert",
            resolution_json={"author": "Frank Herbert"},
            created_from_review_id=review.id,
        )
    )
    await db_session.commit()


async def test_clear_library_empties_every_library_table(db_session) -> None:
    await _seed_full_graph(db_session)

    await library_service.clear_library(db_session)

    for model in (
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
    ):
        assert (await db_session.execute(select(model))).scalars().all() == []


async def test_clear_library_preserves_settings(db_session) -> None:
    repo = SettingsRepository(db_session)
    await repo.set("google_oauth_token_enc", "secret-token")
    await repo.set("drive_inbox_folder_id", "folder-123")

    await _seed_full_graph(db_session)
    await library_service.clear_library(db_session)

    assert await repo.get("google_oauth_token_enc") == "secret-token"
    assert await repo.get("drive_inbox_folder_id") == "folder-123"


async def test_clear_library_is_safe_to_run_on_empty_db(db_session) -> None:
    await library_service.clear_library(db_session)  # must not raise

    assert (await db_session.execute(select(File))).scalars().all() == []


class _FakeSheetProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create_spreadsheet_from_csv(self, *, name: str, csv_bytes: bytes, parent_id: str | None) -> dict:
        self.calls.append({"name": name, "csv_bytes": csv_bytes, "parent_id": parent_id})
        return {"id": "sheet-1", "name": name, "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-1"}


async def test_export_to_sheet_uploads_csv_of_every_tracked_file(db_session) -> None:
    await _seed_full_graph(db_session)
    provider = _FakeSheetProvider()

    result = await library_service.export_to_sheet(db_session, provider, parent_id="lib-folder-id")

    assert provider.calls[0]["parent_id"] == "lib-folder-id"
    csv_text = provider.calls[0]["csv_bytes"].decode("utf-8")
    assert "Title,Author,Series,Series #,Status,Filename,Quality score,Discovered" in csv_text
    assert "Dune" in csv_text
    assert "Frank Herbert" in csv_text
    assert "dune.epub" in csv_text
    assert result.url == "https://docs.google.com/spreadsheets/d/sheet-1"


async def test_export_to_sheet_is_safe_to_run_on_empty_db(db_session) -> None:
    provider = _FakeSheetProvider()

    result = await library_service.export_to_sheet(db_session, provider, parent_id=None)

    assert provider.calls[0]["parent_id"] is None
    assert result.name.startswith("BookBrain Library — ")
