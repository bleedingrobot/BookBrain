import numpy as np
import pytest
from sqlalchemy import select

from app.data.models import Book
from app.services.theme_dedup_service import (
    canonical_themes,
    cluster_themes,
    content_stems,
    normalize_theme,
    refresh_theme_canon,
)


def _embedder(groups: dict[str, float]):
    """Unit vectors in 2D: a theme at angle a·acos(cos) from the x-axis, so
    `groups` maps each theme to its cosine against the anchor [1, 0] and any
    theme not listed sits orthogonal (cosine 0) to everything listed."""

    def embed(texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            if t in groups:
                angle = np.arccos(groups[t])
                out.append([np.cos(angle), np.sin(angle), 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return np.asarray(out, dtype=np.float32)

    return embed


def test_normalize_theme_folds_case_and_whitespace() -> None:
    assert normalize_theme("  Loss  and\tGrief ") == "loss and grief"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("moral dilemma", "moral dilemmas"),
        ("consequences of war", "war and its consequences"),
        ("freedom and confinement", "freedom vs. confinement"),
        ("human-dragon relationships", "human and dragon relationships"),
        ("consequences of actions", "the consequences of one's actions"),
        ("technology and innovation", "technological innovation"),
    ],
)
def test_content_stems_match_rephrasings(a: str, b: str) -> None:
    assert content_stems(a) == content_stems(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("father-daughter relationships", "father-son relationships"),
        ("sacrifice", "love and sacrifice"),
        ("immortality and longevity", "immortality and mortality"),
        ("identity and deception", "identity and self-deception"),
    ],
)
def test_content_stems_keep_distinct_ideas_apart(a: str, b: str) -> None:
    assert content_stems(a) != content_stems(b)


def test_cluster_merges_above_auto_threshold_regardless_of_wording() -> None:
    embed = _embedder({"loyalty and betrayal": 1.0, "trust and treachery": 0.97})
    mapping = cluster_themes(
        {1: ["loyalty and betrayal"], 2: ["loyalty and betrayal"], 3: ["Trust and Treachery"]},
        embed,
    )
    assert mapping == {
        "loyalty and betrayal": "loyalty and betrayal",
        "trust and treachery": "loyalty and betrayal",
    }


def test_cluster_merges_middle_band_only_on_matching_stems() -> None:
    embed = _embedder({"moral dilemma": 1.0, "moral dilemmas": 0.9})
    mapping = cluster_themes({1: ["moral dilemma"], 2: ["moral dilemmas"], 3: ["moral dilemmas"]}, embed)
    assert mapping["moral dilemma"] == "moral dilemmas"


def test_cluster_keeps_middle_band_apart_when_stems_differ_even_if_they_share_a_book() -> None:
    # libtrails' co-occurrence gate would merge these; on BookBrain's data a
    # shared book is evidence the model saw them as different themes.
    embed = _embedder({"espionage": 1.0, "secrets and espionage": 0.876})
    mapping = cluster_themes({1: ["espionage", "secrets and espionage"]}, embed)
    assert mapping["espionage"] == "espionage"
    assert mapping["secrets and espionage"] == "secrets and espionage"


def test_cluster_never_merges_below_lexical_threshold_even_with_matching_stems() -> None:
    embed = _embedder({"moral dilemma": 1.0, "moral dilemmas": 0.8})
    mapping = cluster_themes({1: ["moral dilemma"], 2: ["moral dilemmas"]}, embed)
    assert mapping["moral dilemma"] != mapping["moral dilemmas"]


def test_cluster_label_prefers_most_books_then_shortest() -> None:
    embed = _embedder({"grief and loss": 1.0, "loss and grief": 0.99, "loss & grief": 0.98})
    mapping = cluster_themes({1: ["grief and loss"], 2: ["loss and grief"], 3: ["loss & grief"]}, embed)
    assert set(mapping.values()) == {"loss & grief"}  # all tied on 1 book: shortest wins


def test_cluster_empty_input() -> None:
    assert cluster_themes({}, _embedder({})) == {}


def test_canonical_themes_maps_and_dedups_in_order() -> None:
    mapping = {"grief and loss": "loss and grief", "loss and grief": "loss and grief", "revenge": "revenge"}
    assert canonical_themes(["Revenge", "grief and loss", "loss and grief", " "], mapping) == [
        "revenge",
        "loss and grief",
    ]


def test_canonical_themes_and_clusters_skip_generic_bare_themes() -> None:
    embed = _embedder({"survival against the odds": 1.0})
    mapping = cluster_themes({1: ["Survival", "survival against the odds", "Identity"]}, embed)
    assert mapping == {"survival against the odds": "survival against the odds"}
    assert canonical_themes(["Survival", "survival against the odds", "identity."], mapping) == [
        "survival against the odds"
    ]


def _done(themes: list[str]) -> dict:
    return {"full": {"status": "done", "themes": themes, "genres": ["Fantasy"]}}


async def test_refresh_theme_canon_writes_canonical_themes_and_is_idempotent(db_session) -> None:
    a = Book(canonical_title="A", llm_tags_json=_done(["Loss and grief", "revenge"]))
    b = Book(canonical_title="B", llm_tags_json=_done(["grief and loss", "loss and grief"]))
    c = Book(canonical_title="C", llm_tags_json=_done(["loss and grief"]))
    mapping_in_flight = {"full": {"status": "mapping", "chunksDone": 1, "chunkResults": []}}
    d = Book(canonical_title="D", llm_tags_json=mapping_in_flight)
    db_session.add_all([a, b, c, d])
    await db_session.commit()

    embed = _embedder({"loss and grief": 1.0, "grief and loss": 0.99})
    result = await refresh_theme_canon(db_session, embed_fn=embed)
    assert result == {"books": 3, "themes": 3, "canonical": 2, "booksUpdated": 3}

    rows = {bk.canonical_title: bk for bk in (await db_session.execute(select(Book))).scalars()}
    assert rows["A"].llm_tags_json["full"]["themesCanonical"] == ["loss and grief", "revenge"]
    assert rows["B"].llm_tags_json["full"]["themesCanonical"] == ["loss and grief"]
    # the raw themes and everything else stay as the LLM wrote them
    assert rows["A"].llm_tags_json["full"]["themes"] == ["Loss and grief", "revenge"]
    assert rows["A"].llm_tags_json["full"]["genres"] == ["Fantasy"]
    assert rows["D"].llm_tags_json == mapping_in_flight

    again = await refresh_theme_canon(db_session, embed_fn=embed)
    assert again["booksUpdated"] == 0
