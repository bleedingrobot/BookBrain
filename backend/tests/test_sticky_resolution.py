from sqlalchemy import select

from app.data.models import Author, Book, File, FileStatus, LibraryRule, Review, ReviewStatus, RuleType
from app.providers.epub.parser import EpubEvidence
from app.services.sticky_resolution import find_rule_match, resolve_corrected_book_id


def _evidence(**overrides) -> EpubEvidence:
    defaults = dict(title="Dune", authors=["Frank Herbert"], language="en")
    defaults.update(overrides)
    return EpubEvidence(**defaults)


async def _seed_file(db_session, sha256="abc123") -> File:
    file_row = File(
        drive_file_id="drive-1",
        drive_parent_id="p",
        filename="dune.epub",
        sha256=sha256,
        size_bytes=100,
        status=FileStatus.review,
    )
    db_session.add(file_row)
    await db_session.commit()
    return file_row


async def test_resolve_corrected_book_id_returns_none_when_no_review_exists(db_session) -> None:
    result = await resolve_corrected_book_id(db_session, "abc123")
    assert result is None


async def test_resolve_corrected_book_id_resolves_book_for_matching_sha256(db_session) -> None:
    file_row = await _seed_file(db_session, sha256="matching-hash")
    review = Review(
        file_id=file_row.id,
        status=ReviewStatus.corrected,
        proposed_json={},
        correction_json={"title": "Corrected Title", "author": "Corrected Author"},
    )
    db_session.add(review)
    await db_session.commit()

    book_id = await resolve_corrected_book_id(db_session, "matching-hash")

    assert book_id is not None
    book = (await db_session.execute(select(Book).where(Book.id == book_id))).scalar_one()
    assert book.canonical_title == "Corrected Title"
    author = (await db_session.execute(select(Author).where(Author.id == book.author_id))).scalar_one()
    assert author.name == "Corrected Author"


async def test_resolve_corrected_book_id_ignores_non_corrected_reviews(db_session) -> None:
    file_row = await _seed_file(db_session, sha256="pending-hash")
    review = Review(
        file_id=file_row.id, status=ReviewStatus.pending, proposed_json={}, correction_json=None
    )
    db_session.add(review)
    await db_session.commit()

    result = await resolve_corrected_book_id(db_session, "pending-hash")

    assert result is None


async def test_resolve_corrected_book_id_ignores_different_sha256(db_session) -> None:
    file_row = await _seed_file(db_session, sha256="hash-a")
    review = Review(
        file_id=file_row.id,
        status=ReviewStatus.corrected,
        proposed_json={},
        correction_json={"title": "X"},
    )
    db_session.add(review)
    await db_session.commit()

    result = await resolve_corrected_book_id(db_session, "hash-b")

    assert result is None


async def test_find_rule_match_returns_none_with_no_rules(db_session) -> None:
    result = await find_rule_match(db_session, "dune.epub", _evidence())
    assert result is None


async def test_find_rule_match_applies_author_alias(db_session) -> None:
    db_session.add(
        LibraryRule(
            rule_type=RuleType.author_alias,
            pattern="Frank Herbert",
            resolution_json={"author": "Frank P. Herbert"},
        )
    )
    await db_session.commit()

    result = await find_rule_match(db_session, "dune.epub", _evidence())

    assert result is not None
    assert result.author == "Frank P. Herbert"
    assert result.computed_confidence == 100
    assert result.model == "library_rule"


async def test_find_rule_match_applies_series_alias(db_session) -> None:
    db_session.add(
        LibraryRule(
            rule_type=RuleType.series_alias,
            pattern="Dune Saga",
            resolution_json={"series": "Dune Chronicles"},
        )
    )
    await db_session.commit()

    result = await find_rule_match(db_session, "dune.epub", _evidence(series="Dune Saga"))

    assert result is not None
    assert result.series == "Dune Chronicles"


async def test_find_rule_match_case_insensitive(db_session) -> None:
    db_session.add(
        LibraryRule(
            rule_type=RuleType.author_alias,
            pattern="frank herbert",
            resolution_json={"author": "Frank Herbert (canonical)"},
        )
    )
    await db_session.commit()

    result = await find_rule_match(db_session, "dune.epub", _evidence())

    assert result is not None
    assert result.author == "Frank Herbert (canonical)"


async def test_find_rule_match_no_match_leaves_evidence_values(db_session) -> None:
    db_session.add(
        LibraryRule(
            rule_type=RuleType.author_alias,
            pattern="Someone Else",
            resolution_json={"author": "Nope"},
        )
    )
    await db_session.commit()

    result = await find_rule_match(db_session, "dune.epub", _evidence())

    assert result is None
