from sqlalchemy import select

from app.data.models import Book
from app.services.tag_vocab import (
    GENRE_MAP,
    GENRES,
    MOOD_MAP,
    MOODS,
    OTHER,
    canonical_fields,
    curate_genres,
    curate_moods,
    refresh_tag_vocab,
)


def test_every_map_target_is_in_the_vocabulary() -> None:
    assert {g for gs in GENRE_MAP.values() for g in gs} <= {*GENRES, OTHER}
    assert {m for ms in MOOD_MAP.values() for m in ms} <= set(MOODS)


def test_curate_genres_maps_drops_and_routes_to_other() -> None:
    genres, other = curate_genres(
        ["Sci-Fi", "Police Procedural", " drama ", "Poetry", "Mystery", "Solarpunk", "poetry"]
    )
    assert genres == ["Science Fiction", "Mystery", "Crime"]
    # an OTHER mapping and a value the map has never seen both stay reviewable
    assert other == ["Poetry", "Solarpunk"]


def test_curate_moods_drops_character_states_and_unknowns() -> None:
    assert curate_moods(["Suspenseful", "tense", "desperate", "darkly humorous", "zesty"]) == [
        "tense",
        "funny",
        "dark",
    ]


def test_canonical_fields_are_not_truncated_to_the_caps() -> None:
    full = {
        "genres": ["Fantasy", "Horror", "Mystery", "Thriller", "Romance", "Western"],
        "moods": ["tense", "dark", "gritty", "scary", "epic", "cozy", "funny"],
        "otherGenre": ["High Fantasy", "Solarpunk"],
    }
    fields = canonical_fields(full)
    assert fields["genresCanonical"] == [*full["genres"], "Epic Fantasy"]
    assert fields["moodsCanonical"] == full["moods"]
    assert fields["otherGenreCanonical"] == ["Solarpunk"]


async def test_refresh_writes_curated_fields_beside_untouched_raw_ones(db_session) -> None:
    raw = {"status": "done", "genres": ["Poetry"], "moods": ["Neutral"], "themes": ["x"]}
    aura = Book(canonical_title="Aura", llm_tags_json={"full": dict(raw), "excerpt": {"k": 1}})
    mid = Book(
        canonical_title="Mid",
        llm_tags_json={"full": {"status": "mapping", "chunkResults": [{"genres": ["Sci-Fi"]}]}},
    )
    db_session.add_all([aura, mid])
    await db_session.commit()

    result = await refresh_tag_vocab(db_session)
    assert result["books"] == 1 and result["booksUpdated"] == 1
    assert result["withoutGenres"] == ["Aura"] and result["withoutMoods"] == ["Aura"]
    assert result["unmappedGenres"] == [] and result["unmappedMoods"] == []

    aura = (await db_session.execute(select(Book).where(Book.canonical_title == "Aura"))).scalar_one()
    full = aura.llm_tags_json["full"]
    assert {k: full[k] for k in raw} == raw  # raw values untouched
    assert full["genresCanonical"] == [] and full["moodsCanonical"] == []
    assert full["otherGenreCanonical"] == ["Poetry"]
    assert aura.llm_tags_json["excerpt"] == {"k": 1}
    mid = (await db_session.execute(select(Book).where(Book.canonical_title == "Mid"))).scalar_one()
    assert "genresCanonical" not in mid.llm_tags_json["full"]  # in-progress books left alone

    again = await refresh_tag_vocab(db_session)
    assert again["booksUpdated"] == 0  # idempotent


async def test_refresh_reports_raw_values_the_map_has_never_seen(db_session) -> None:
    db_session.add(
        Book(
            canonical_title="New",
            llm_tags_json={"full": {"status": "done", "genres": ["Solarpunk"], "moods": ["zesty"]}},
        )
    )
    await db_session.commit()
    result = await refresh_tag_vocab(db_session)
    assert result["unmappedGenres"] == ["Solarpunk"]
    assert result["unmappedMoods"] == ["zesty"]
