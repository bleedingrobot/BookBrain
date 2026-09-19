from app.services.tag_vocab import (
    GENRE_MAP,
    GENRES,
    MOOD_MAP,
    MOODS,
    OTHER,
    canonical_fields,
    curate_genres,
    curate_moods,
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

