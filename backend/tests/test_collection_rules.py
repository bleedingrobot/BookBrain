import pytest

from app.data.models import Author, Book, Series
from app.services.collection_rules import CollectionRuleError, parse, resolve


async def _seed(db_session):
    author = Author(name="Terry Pratchett")
    series = Series(name="Discworld")
    db_session.add_all([author, series])
    await db_session.flush()

    db_session.add_all(
        [
            Book(
                canonical_title="Guards! Guards!",
                author_id=author.id,
                series_id=series.id,
                series_number=8,
                language="en",
                hardcover_json={"meta": {"genres": ["Fantasy", "Comedy"], "rating": 4.4}},
            ),
            Book(
                canonical_title="Mort",
                author_id=author.id,
                series_id=series.id,
                series_number=4,
                language="en",
                hardcover_json={"meta": {"genres": ["Fantasy"], "rating": 4.2}},
            ),
            Book(
                canonical_title="Dune",
                language="en",
                hardcover_json={"meta": {"genres": ["Science Fiction"], "rating": 4.8}},
            ),
        ]
    )
    await db_session.commit()


# --- parser ---


def test_parses_single_clause():
    rule = parse("series:Discworld")
    assert rule.combinator == "AND"
    assert rule.clauses[0].field == "series"
    assert rule.clauses[0].value == "Discworld"


def test_parses_and_clauses():
    rule = parse("genre:Fantasy AND rating>=4")
    assert rule.combinator == "AND"
    assert [c.field for c in rule.clauses] == ["genre", "rating"]


def test_parses_or_clauses_with_quoted_values():
    rule = parse('mood:"cozy" OR mood:"hopeful"')
    assert rule.combinator == "OR"
    assert [c.value for c in rule.clauses] == ["cozy", "hopeful"]


def test_parses_negated_clause():
    rule = parse("genre:Fantasy AND -mood:dark")
    assert rule.clauses[1].negate is True


def test_rejects_mixed_and_or():
    with pytest.raises(CollectionRuleError, match="not both"):
        parse("genre:Fantasy AND mood:dark OR mood:cozy")


def test_rejects_unknown_field():
    with pytest.raises(CollectionRuleError, match="not a valid field"):
        parse("nonsense:Fantasy")


def test_rejects_empty_rule():
    with pytest.raises(CollectionRuleError):
        parse("   ")


def test_rejects_bad_comparator_on_text_field():
    with pytest.raises(CollectionRuleError, match="does not support"):
        parse("title>=Dune")


def test_rejects_non_numeric_value_for_rating():
    with pytest.raises(CollectionRuleError, match="not a valid number"):
        parse("rating>=great")


# --- resolver ---


async def test_resolve_matches_series(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("series:Discworld"))
    assert {b.canonical_title for b in books} == {"Guards! Guards!", "Mort"}


async def test_resolve_matches_genre_membership(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("genre:Fantasy"))
    assert {b.canonical_title for b in books} == {"Guards! Guards!", "Mort"}


async def test_resolve_and_combinator_narrows_results(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("genre:Fantasy AND rating>=4.3"))
    assert {b.canonical_title for b in books} == {"Guards! Guards!"}


async def test_resolve_or_combinator_widens_results(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("series:Discworld OR genre:Science Fiction"))
    assert {b.canonical_title for b in books} == {"Guards! Guards!", "Mort", "Dune"}


async def test_resolve_negation_excludes(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("genre:Fantasy AND -title:Mort"))
    assert {b.canonical_title for b in books} == {"Guards! Guards!"}


async def test_resolve_no_matches_returns_empty(db_session):
    await _seed(db_session)
    books = await resolve(db_session, parse("series:Nonexistent"))
    assert books == []
