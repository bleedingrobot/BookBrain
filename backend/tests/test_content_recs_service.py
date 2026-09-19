from sqlalchemy import select

from app.data.models import Book, File, FileStatus, Series
from app.services.content_recs_service import refresh_content_recs


async def _seed(
    db_session,
    title: str,
    *,
    genres: list[str] | None = None,
    moods: list[str] | None = None,
    representation: list[str] | None = None,
    themes: list[str] | None = None,
    content_warnings: list[str] | None = None,
    done: bool = True,
    organised: bool = True,
    series_id: int | None = None,
) -> Book:
    book = Book(canonical_title=title, series_id=series_id)
    db_session.add(book)
    await db_session.flush()
    if done:
        book.llm_tags_json = {
            "full": {
                "status": "done",
                "genres": genres or [],
                "moods": moods or [],
                "representation": representation or [],
                "themes": themes or [],
                "contentWarnings": content_warnings or [],
                "shortDescription": "x",
                "longSummary": "y",
            }
        }
    db_session.add(
        File(
            drive_file_id=f"d-{title}",
            filename=f"{title}.epub",
            sha256=title,
            size_bytes=1,
            status=FileStatus.organised if organised else FileStatus.inbox,
            book_id=book.id,
        )
    )
    await db_session.commit()
    return book


async def _refresh_book(db_session, book: Book) -> Book:
    await refresh_content_recs(db_session)
    await db_session.refresh(book)
    return book


async def test_books_sharing_genres_and_moods_match_each_other(db_session) -> None:
    a = await _seed(db_session, "A", genres=["Fantasy"], moods=["dark"])
    b = await _seed(db_session, "B", genres=["Fantasy"], moods=["dark"])
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    await db_session.refresh(b)

    a_similar = a.content_recs_json["similar"]
    assert len(a_similar) == 1
    assert a_similar[0]["bookId"] == b.id
    assert set(a_similar[0]["sharedTags"]) == {"Fantasy", "dark"}
    assert a_similar[0]["score"] > 0


async def test_below_min_shared_tags_does_not_match(db_session) -> None:
    a = await _seed(db_session, "A", genres=["Fantasy"])
    b = await _seed(db_session, "B", genres=["Fantasy"], moods=["dark"])
    # A and B share only one scored tag (Fantasy) — below _MIN_SHARED_TAGS=2
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    assert a.content_recs_json["similar"] == []


async def test_themes_are_not_scored_but_appear_as_bonus_shared_tags(db_session) -> None:
    a = await _seed(db_session, "A", genres=["Fantasy"], moods=["dark"], themes=["revenge"])
    b = await _seed(db_session, "B", genres=["Fantasy"], moods=["dark"], themes=["revenge"])
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    shared = set(a.content_recs_json["similar"][0]["sharedTags"])
    assert "revenge" in shared  # theme match surfaced for explainability
    assert {"Fantasy", "dark"} <= shared


async def test_a_book_with_only_themes_has_no_scored_vector_and_no_matches(db_session) -> None:
    # Nothing in genres/moods/representation -> excluded from the similarity
    # matrix entirely, even though `_llm_tags` itself is non-empty.
    a = await _seed(db_session, "A", themes=["revenge"])
    b = await _seed(db_session, "B", themes=["revenge"])
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    assert a.content_recs_json == {"similar": [], "generatedAt": a.content_recs_json["generatedAt"]}


async def test_content_warnings_never_influence_matching(db_session) -> None:
    a = await _seed(db_session, "A", genres=["Fantasy"], moods=["dark"], content_warnings=["violence"])
    b = await _seed(db_session, "B", content_warnings=["violence"])  # nothing else in common
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    assert a.content_recs_json["similar"] == []


async def test_common_tag_is_downweighted_relative_to_a_rare_one(db_session) -> None:
    # "Fantasy" and "dark" both appear on several books (low IDF); "whimsical"
    # appears only on Source + RareMatch (high IDF). Both CommonOnly and
    # RareMatch share exactly 2 scored tags with Source, but RareMatch's
    # shared tag is the rare one, so it should score higher.
    source = await _seed(db_session, "Source", genres=["Fantasy"], moods=["whimsical", "dark"])
    common_only = await _seed(db_session, "CommonOnly", genres=["Fantasy"], moods=["dark"])
    rare_match = await _seed(db_session, "RareMatch", genres=["Fantasy"], moods=["whimsical"])
    # Pad the pool so "Fantasy"/"dark" are common while "whimsical" stays rare.
    for i in range(5):
        await _seed(db_session, f"Filler{i}", genres=["Fantasy"], moods=["dark"])

    await refresh_content_recs(db_session)
    await db_session.refresh(source)
    similar = {m["bookId"]: m["score"] for m in source.content_recs_json["similar"]}
    assert similar[rare_match.id] > similar[common_only.id]


async def test_series_diversity_cap(db_session) -> None:
    series = Series(name="Trilogy")
    db_session.add(series)
    await db_session.flush()

    hub = await _seed(db_session, "Hub", genres=["Fantasy"], moods=["dark"])
    for i in range(3):
        await _seed(
            db_session, f"Trilogy{i}", genres=["Fantasy"], moods=["dark"], series_id=series.id
        )
    await refresh_content_recs(db_session)
    await db_session.refresh(hub)
    assert len(hub.content_recs_json["similar"]) == 2  # capped, not 3


async def test_unorganised_and_untagged_books_are_excluded(db_session) -> None:
    a = await _seed(db_session, "A", genres=["Fantasy"], moods=["dark"])
    await _seed(db_session, "NotOrganised", genres=["Fantasy"], moods=["dark"], organised=False)
    await _seed(db_session, "NotTagged", genres=["Fantasy"], moods=["dark"], done=False)
    await refresh_content_recs(db_session)
    await db_session.refresh(a)
    assert a.content_recs_json["similar"] == []


async def test_refresh_returns_summary_counts(db_session) -> None:
    await _seed(db_session, "A", genres=["Fantasy"], moods=["dark"])
    await _seed(db_session, "B", genres=["Fantasy"], moods=["dark"])
    await _seed(db_session, "C", themes=["only a theme, no scored tags"])
    result = await refresh_content_recs(db_session)
    assert result == {"books": 3, "withMatches": 2}


async def test_raw_synonyms_match_through_the_curated_vocabulary(db_session) -> None:
    # prompts/47 A.1 — scored on genresCanonical/moodsCanonical, so books
    # tagged before the enum ("Sci-Fi", "suspenseful") match newer ones.
    a = await _seed(db_session, "A", genres=["Sci-Fi"], moods=["suspenseful", "desperate"])
    b = await _seed(db_session, "B", genres=["Science Fiction"], moods=["tense"])
    a = await _refresh_book(db_session, a)
    [match] = a.content_recs_json["similar"]
    assert match["bookId"] == b.id
    assert match["sharedTags"] == ["Science Fiction", "tense"]
