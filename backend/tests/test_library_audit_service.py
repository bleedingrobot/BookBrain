from app.data.models import Author, Book, File, Series
from app.services.library_audit_service import audit_library


def _file(drive_id: str) -> File:
    return File(
        drive_file_id=drive_id,
        filename=f"{drive_id}.epub",
        sha256=drive_id * 8,
        size_bytes=100,
    )


async def test_audit_flags_series_that_differ_by_a_word(db_session) -> None:
    dune = Series(name="Dune")
    dune_chronicles = Series(name="Dune Chronicles")
    db_session.add_all([dune, dune_chronicles])
    await db_session.flush()

    db_session.add_all(
        [
            Book(canonical_title="Dune", series_id=dune.id, files=[_file("d1")]),
            Book(
                canonical_title="Dune Messiah",
                series_id=dune_chronicles.id,
                files=[_file("d2"), _file("d3")],
            ),
        ]
    )
    await db_session.commit()

    result = await audit_library(db_session)

    assert len(result.similar_series) == 1
    names = {m.name for m in result.similar_series[0].members}
    assert names == {"Dune", "Dune Chronicles"}
    counts = {m.name: (m.book_count, m.file_count) for m in result.similar_series[0].members}
    assert counts["Dune"] == (1, 1)
    assert counts["Dune Chronicles"] == (1, 2)
    assert result.similar_authors == []


async def test_audit_flags_series_that_differ_by_a_typo(db_session) -> None:
    a = Series(name="The Wheel of Time")
    b = Series(name="The Wheell of Time")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all(
        [
            Book(canonical_title="Book One", series_id=a.id, files=[_file("w1")]),
            Book(canonical_title="Book Two", series_id=b.id, files=[_file("w2")]),
        ]
    )
    await db_session.commit()

    result = await audit_library(db_session)

    assert len(result.similar_series) == 1


async def test_audit_does_not_flag_unrelated_series(db_session) -> None:
    db_session.add_all([Series(name="Dune"), Series(name="Discworld")])
    await db_session.commit()

    result = await audit_library(db_session)

    assert result.similar_series == []


async def test_audit_flags_similar_authors(db_session) -> None:
    a = Author(name="Terry Pratchet")
    b = Author(name="Terry Pratchett")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all(
        [
            Book(canonical_title="Book One", author_id=a.id, files=[_file("p1")]),
            Book(canonical_title="Book Two", author_id=b.id, files=[_file("p2")]),
        ]
    )
    await db_session.commit()

    result = await audit_library(db_session)

    assert len(result.similar_authors) == 1
    assert result.similar_series == []


async def test_audit_clusters_more_than_two_similar_names(db_session) -> None:
    db_session.add_all(
        [Series(name="Dune"), Series(name="Dune Chronicles"), Series(name="Dune Saga")]
    )
    await db_session.commit()

    result = await audit_library(db_session)

    assert len(result.similar_series) == 1
    assert len(result.similar_series[0].members) == 3


async def test_audit_ignores_very_short_names(db_session) -> None:
    # Short names produce noisy/meaningless SequenceMatcher ratios — "It" vs
    # "I" would otherwise flag on almost nothing.
    db_session.add_all([Series(name="It"), Series(name="I")])
    await db_session.commit()

    result = await audit_library(db_session)

    assert result.similar_series == []


async def test_audit_returns_empty_on_empty_library(db_session) -> None:
    result = await audit_library(db_session)

    assert result.similar_series == []
    assert result.similar_authors == []
