"""CRUD for smart collections (named, rule-based shelves — see
app.services.collection_rules) plus a non-persisting preview so the admin can
check a rule's matches before saving it."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Book, File, FileStatus, SmartCollection
from app.schemas.smart_collections import (
    RulePreviewResult,
    SmartCollectionCreate,
    SmartCollectionUpdate,
)
from app.services.collection_rules import build_query, parse

_PREVIEW_SAMPLE_SIZE = 20


class SmartCollectionNotFound(Exception):
    pass


class SmartCollectionNameTaken(Exception):
    pass


async def list_collections(session: AsyncSession) -> list[SmartCollection]:
    return list(
        (
            await session.execute(select(SmartCollection).order_by(SmartCollection.name))
        ).scalars()
    )


async def _check_name_available(session: AsyncSession, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(SmartCollection.id).where(SmartCollection.name == name)
    if exclude_id is not None:
        stmt = stmt.where(SmartCollection.id != exclude_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise SmartCollectionNameTaken(f"a collection named {name!r} already exists")


async def create(session: AsyncSession, body: SmartCollectionCreate) -> SmartCollection:
    parse(body.rule)  # raises CollectionRuleError on an invalid rule
    await _check_name_available(session, body.name)

    collection = SmartCollection(name=body.name, description=body.description, rule=body.rule)
    session.add(collection)
    await session.commit()
    await session.refresh(collection)
    return collection


async def update(session: AsyncSession, collection_id: int, body: SmartCollectionUpdate) -> SmartCollection:
    collection = await session.get(SmartCollection, collection_id)
    if collection is None:
        raise SmartCollectionNotFound(f"smart collection {collection_id} not found")

    if body.rule is not None:
        parse(body.rule)  # raises CollectionRuleError on an invalid rule
        collection.rule = body.rule
    if body.name is not None and body.name != collection.name:
        await _check_name_available(session, body.name, exclude_id=collection_id)
        collection.name = body.name
    if body.description is not None:
        collection.description = body.description

    await session.commit()
    await session.refresh(collection)
    return collection


async def delete(session: AsyncSession, collection_id: int) -> None:
    collection = await session.get(SmartCollection, collection_id)
    if collection is None:
        raise SmartCollectionNotFound(f"smart collection {collection_id} not found")
    await session.delete(collection)
    await session.commit()


async def _organised_titles_matching(session: AsyncSession, rule_str: str) -> list[str]:
    """Titles of organised, in-library books matching the rule — what would
    actually show up for the family viewer, not every Book row (a Book can
    outlive its File, e.g. after a rejection)."""
    rule = parse(rule_str)
    stmt = (
        build_query(rule)
        .join(File, File.book_id == Book.id)
        .where(File.status == FileStatus.organised)
    )
    rows = (await session.execute(stmt)).scalars().unique().all()
    return [b.canonical_title for b in rows]


async def preview(session: AsyncSession, rule_str: str) -> RulePreviewResult:
    """Raises CollectionRuleError (from parse()) on an invalid rule — the
    caller surfaces that message verbatim, same as create()/update()."""
    titles = await _organised_titles_matching(session, rule_str)
    return RulePreviewResult(count=len(titles), sample=sorted(titles)[:_PREVIEW_SAMPLE_SIZE])
