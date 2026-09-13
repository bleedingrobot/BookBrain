import pytest

from app.data.models import Book, File, FileStatus
from app.schemas.smart_collections import SmartCollectionCreate, SmartCollectionUpdate
from app.services import smart_collections_service
from app.services.collection_rules import CollectionRuleError


async def _seed_organised_book(db_session, *, title: str, genres: list[str]) -> None:
    book = Book(canonical_title=title, hardcover_json={"meta": {"genres": genres}})
    db_session.add(book)
    await db_session.flush()
    db_session.add(
        File(
            drive_file_id=f"drive-{title}",
            filename=f"{title}.epub",
            sha256=f"sha-{title}",
            size_bytes=100,
            status=FileStatus.organised,
            book_id=book.id,
        )
    )
    await db_session.commit()


async def test_create_rejects_invalid_rule(db_session):
    with pytest.raises(CollectionRuleError):
        await smart_collections_service.create(
            db_session, SmartCollectionCreate(name="Bad", rule="nonsense:Fantasy")
        )


async def test_create_and_list(db_session):
    created = await smart_collections_service.create(
        db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Fantasy")
    )
    assert created.id is not None

    listed = await smart_collections_service.list_collections(db_session)
    assert [c.name for c in listed] == ["Fantasy"]


async def test_create_rejects_duplicate_name(db_session):
    await smart_collections_service.create(
        db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Fantasy")
    )
    with pytest.raises(smart_collections_service.SmartCollectionNameTaken):
        await smart_collections_service.create(
            db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Horror")
        )


async def test_update_rejects_invalid_rule(db_session):
    created = await smart_collections_service.create(
        db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Fantasy")
    )
    with pytest.raises(CollectionRuleError):
        await smart_collections_service.update(
            db_session, created.id, SmartCollectionUpdate(rule="nonsense:Fantasy")
        )


async def test_update_changes_rule_and_name(db_session):
    created = await smart_collections_service.create(
        db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Fantasy")
    )
    updated = await smart_collections_service.update(
        db_session, created.id, SmartCollectionUpdate(name="Sci-Fi", rule="genre:Science Fiction")
    )
    assert updated.name == "Sci-Fi"
    assert updated.rule == "genre:Science Fiction"


async def test_update_raises_for_missing_id(db_session):
    with pytest.raises(smart_collections_service.SmartCollectionNotFound):
        await smart_collections_service.update(db_session, 999, SmartCollectionUpdate(name="X"))


async def test_delete_removes_collection(db_session):
    created = await smart_collections_service.create(
        db_session, SmartCollectionCreate(name="Fantasy", rule="genre:Fantasy")
    )
    await smart_collections_service.delete(db_session, created.id)
    assert await smart_collections_service.list_collections(db_session) == []


async def test_delete_raises_for_missing_id(db_session):
    with pytest.raises(smart_collections_service.SmartCollectionNotFound):
        await smart_collections_service.delete(db_session, 999)


async def test_preview_only_counts_organised_books(db_session):
    await _seed_organised_book(db_session, title="Dune", genres=["Science Fiction"])
    # An un-organised Book (no File row) matching the rule must not count.
    db_session.add(Book(canonical_title="Foundation", hardcover_json={"meta": {"genres": ["Science Fiction"]}}))
    await db_session.commit()

    result = await smart_collections_service.preview(db_session, "genre:Science Fiction")
    assert result.count == 1
    assert result.sample == ["Dune"]


async def test_preview_raises_for_invalid_rule(db_session):
    with pytest.raises(CollectionRuleError):
        await smart_collections_service.preview(db_session, "nonsense:Fantasy")
