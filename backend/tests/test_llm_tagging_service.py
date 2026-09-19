from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.data.models import Book, File, FileStatus
from app.services.llm_tagging_service import (
    _in_allowed_window,
    _is_pending,
    _local_now,
    chunk_documents,
    select_work_item,
    validate_chunk_result,
    validate_tag_result,
)


def _file_for(book: Book) -> File:
    file = File(drive_file_id=f"d-{id(book)}", filename="b.epub", sha256="x", size_bytes=1)
    file.status = FileStatus.organised
    file.book = book
    return file


# --------------------------------------------------------------------------
# allowed windows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "iso, expected",
    [
        ("2026-09-14T02:00:00", True),  # Monday 2am - overnight
        ("2026-09-14T23:30:00", True),  # Monday 11:30pm - overnight
        ("2026-09-14T21:00:00", True),  # Monday 9pm exactly - evening window opens
        ("2026-09-14T20:59:00", False),  # Monday 8:59pm - just before evening window
        ("2026-09-14T10:00:00", True),  # Monday 10am - workday window
        ("2026-09-14T08:00:00", False),  # Monday 8am - just before window
        ("2026-09-14T15:00:00", False),  # Monday 3pm - just after window
        ("2026-09-19T10:00:00", False),  # Saturday 10am - weekend daytime, not evening
        ("2026-09-19T21:30:00", True),  # Saturday 9:30pm - evening is always allowed
    ],
)
def test_in_allowed_window(iso: str, expected: bool) -> None:
    assert _in_allowed_window(datetime.fromisoformat(iso)) is expected


def test_local_now_uses_configured_timezone_not_server_clock(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_tagging_timezone", "Pacific/Auckland")
    now = _local_now(settings)
    # Auckland is UTC+12/+13 — never the same wall-clock hour as UTC itself,
    # which is exactly the bug this guards against (the server runs on UTC).
    assert now.utcoffset() is not None
    assert now.utcoffset().total_seconds() != 0


def test_local_now_falls_back_to_utc_for_an_unknown_timezone(monkeypatch, caplog) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_tagging_timezone", "Not/A_Real_Zone")
    with caplog.at_level("WARNING"):
        now = _local_now(settings)
    assert now.utcoffset().total_seconds() == 0
    assert "Not/A_Real_Zone" in caplog.text


# --------------------------------------------------------------------------
# chunk_documents
# --------------------------------------------------------------------------


def test_chunk_documents_groups_small_docs_together() -> None:
    docs = ["a" * 100, "b" * 100, "c" * 100]
    chunks = chunk_documents(docs, target_chars=250)
    assert len(chunks) == 2
    assert "".join(chunks).replace("\n\n", "") == "".join(docs)


def test_chunk_documents_hard_splits_an_oversized_document() -> None:
    docs = ["x" * 500]
    chunks = chunk_documents(docs, target_chars=200)
    assert len(chunks) == 3
    assert "".join(chunks) == docs[0]


def test_chunk_documents_empty_input() -> None:
    assert chunk_documents([], target_chars=100) == []


def test_chunk_documents_packs_an_oversized_docs_remainder_with_the_next_doc() -> None:
    # Old behaviour: [x200, x100, y150] — three calls for 450 chars.
    chunks = chunk_documents(["x" * 300, "y" * 150], target_chars=260)
    assert len(chunks) == 2
    assert all(len(c) <= 260 for c in chunks)
    assert "".join(chunks).replace("\n", "") == "x" * 300 + "y" * 150


def test_chunk_documents_prefers_a_line_break_near_the_budget() -> None:
    doc = "a" * 95 + "\n" + "b" * 50
    chunks = chunk_documents([doc], target_chars=100)
    assert chunks == ["a" * 95, "b" * 50]


def test_chunk_documents_never_exceeds_the_budget_on_real_sized_input() -> None:
    docs = [("para " * 40 + "\n") * n for n in (3, 250, 180, 20, 400, 5)]
    chunks = chunk_documents(docs, target_chars=26_768)
    assert all(len(c) <= 26_768 for c in chunks)
    assert "".join("".join(chunks).split()) == "".join("".join(docs).split())  # no text lost


# --------------------------------------------------------------------------
# response validation
# --------------------------------------------------------------------------


def _valid_tag_payload() -> dict:
    return {
        "ageRating": "Teen",
        "genres": ["Fantasy"],
        "otherGenre": [],
        "moods": ["dark"],
        "themes": ["revenge"],
        "representation": [],
        "contentWarnings": ["violence"],
        "confidenceNotes": "seemed confident",
        "shortDescription": "A short spoiler-free blurb.",
        "longSummary": "A longer summary including the ending.",
    }


def test_validate_tag_result_happy_path() -> None:
    out = validate_tag_result(_valid_tag_payload())
    assert out["genres"] == ["Fantasy"]
    assert out["shortDescription"] == "A short spoiler-free blurb."


def test_validate_tag_result_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        validate_tag_result([])  # type: ignore[arg-type]


def test_validate_tag_result_rejects_non_list_field() -> None:
    payload = _valid_tag_payload()
    payload["genres"] = "Fantasy"
    with pytest.raises(ValueError):
        validate_tag_result(payload)


def test_validate_tag_result_requires_descriptions() -> None:
    payload = _valid_tag_payload()
    payload["shortDescription"] = ""
    with pytest.raises(ValueError):
        validate_tag_result(payload)


def test_validate_chunk_result_tolerates_missing_lists() -> None:
    out = validate_chunk_result({"chunkSummary": "stuff happened"})
    assert out["chunkSummary"] == "stuff happened"
    assert out["genres"] == []


def test_validate_chunk_result_rejects_non_dict() -> None:
    with pytest.raises(ValueError):
        validate_chunk_result("nope")  # type: ignore[arg-type]


def test_validators_drop_bare_generic_themes_only() -> None:
    themes = ["Identity", "survival.", "survival against supernatural threats", "revenge", " Love "]
    payload = {**_valid_tag_payload(), "themes": themes}
    assert validate_tag_result(payload)["themes"] == ["survival against supernatural threats", "revenge"]
    chunk = validate_chunk_result({"chunkSummary": "x", "themes": themes})
    assert chunk["themes"] == ["survival against supernatural threats", "revenge"]


def test_prompts_tell_the_model_not_to_emit_bare_themes() -> None:
    from types import SimpleNamespace

    from app.services.llm_tagging_service import _SCHEMA_INSTRUCTIONS, _THEME_RULE, _build_map_prompt

    book = SimpleNamespace(canonical_title="T", author=None)
    assert _THEME_RULE in _SCHEMA_INSTRUCTIONS
    assert _THEME_RULE in _build_map_prompt(book, "text", 0, 1)


# --------------------------------------------------------------------------
# work selection
# --------------------------------------------------------------------------


def test_select_work_item_resumes_in_progress_pass_first() -> None:
    now = datetime.now(UTC)
    untouched_book = Book(canonical_title="A", llm_tags_json=None)
    in_progress_book = Book(
        canonical_title="B",
        llm_tags_json={"full": {"status": "mapping", "chunksDone": 1, "chunksTotal": 5}},
    )
    rows = [_file_for(untouched_book), _file_for(in_progress_book)]

    file = select_work_item(rows, now)

    assert file is not None
    assert file.book is in_progress_book


def test_select_work_item_picks_oldest_untouched_book() -> None:
    now = datetime.now(UTC)
    book = Book(canonical_title="A", llm_tags_json=None)
    rows = [_file_for(book)]

    file = select_work_item(rows, now)

    assert file is not None
    assert file.book is book


def test_select_work_item_returns_none_when_everything_is_done() -> None:
    now = datetime.now(UTC)
    book = Book(canonical_title="A", llm_tags_json={"full": {"status": "done", "genres": []}})
    rows = [_file_for(book)]

    assert select_work_item(rows, now) is None


def test_select_work_item_skips_recently_failed_items() -> None:
    now = datetime.now(UTC)
    recent_fail = (now - timedelta(hours=1)).isoformat()
    book = Book(
        canonical_title="A",
        llm_tags_json={"full": {"error": "boom", "failedAt": recent_fail}},
    )
    rows = [_file_for(book)]

    assert select_work_item(rows, now) is None


def test_select_work_item_retries_old_failures() -> None:
    now = datetime.now(UTC)
    old_fail = (now - timedelta(hours=48)).isoformat()
    book = Book(
        canonical_title="A",
        llm_tags_json={"full": {"error": "boom", "failedAt": old_fail}},
    )
    rows = [_file_for(book)]

    file = select_work_item(rows, now)
    assert file is not None
    assert file.book is book


def test_is_pending_treats_missing_entry_as_pending() -> None:
    assert _is_pending(None, datetime.now(UTC)) is True


async def test_theme_refresh_after_reduce_never_raises(monkeypatch, caplog) -> None:
    from app.services import llm_tagging_service, theme_dedup_service

    async def boom(session):
        raise RuntimeError("model download failed")

    monkeypatch.setattr(theme_dedup_service, "refresh_theme_canon", boom)
    await llm_tagging_service._refresh_theme_canon()
    assert "theme dedup refresh failed" in caplog.text


class _RecordingClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.schemas: list[dict | None] = []

    async def generate_json(self, *, system: str, prompt: str, schema: dict | None = None) -> dict:
        self.schemas.append(schema)
        return self.responses.pop(0)


async def test_full_step_constrains_map_and_reduce_with_their_schemas(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services import llm_tagging_service as svc

    async def one_doc(data, settings):
        return ["Some text."]

    monkeypatch.setattr(svc, "_extract_documents", one_doc)
    book = SimpleNamespace(canonical_title="T", author=None, llm_tags_json={})
    client = _RecordingClient([{"chunkSummary": "s"}, _valid_tag_payload()])
    await svc._run_full_step(client, book, b"", get_settings())
    await svc._run_full_step(client, book, b"", get_settings())
    assert client.schemas == [svc.CHUNK_RESULT_SCHEMA, svc.TAG_RESULT_SCHEMA]
    assert book.llm_tags_json["full"]["status"] == "done"


def test_schemas_require_every_field_the_validators_read() -> None:
    from app.services.llm_tagging_service import CHUNK_RESULT_SCHEMA, TAG_RESULT_SCHEMA

    assert set(CHUNK_RESULT_SCHEMA["required"]) == set(CHUNK_RESULT_SCHEMA["properties"])
    assert set(TAG_RESULT_SCHEMA["required"]) == set(TAG_RESULT_SCHEMA["properties"])
    payload = _valid_tag_payload()
    assert set(payload) == set(TAG_RESULT_SCHEMA["properties"])


async def test_book_bytes_downloads_the_in_flight_book_once(monkeypatch) -> None:
    from app.services import llm_tagging_service as svc

    calls: list[str] = []

    def fake_download(creds, file_id, *, timeout_seconds):
        calls.append(file_id)
        return f"epub-{file_id}".encode()

    monkeypatch.setattr(svc, "download_file_with_hard_timeout", fake_download)
    monkeypatch.setattr(svc, "_download_cache", None)
    assert await svc._book_bytes(None, "a") == b"epub-a"
    assert await svc._book_bytes(None, "a") == b"epub-a"
    assert await svc._book_bytes(None, "b") == b"epub-b"
    assert calls == ["a", "b"]


# --------------------------------------------------------------------------
# prompts/47 A.1 — the controlled genre/mood vocabulary
# --------------------------------------------------------------------------


def test_schemas_confine_genres_and_moods_to_the_capped_enum() -> None:
    from app.services import tag_vocab
    from app.services.llm_tagging_service import CHUNK_RESULT_SCHEMA, TAG_RESULT_SCHEMA

    for schema in (CHUNK_RESULT_SCHEMA, TAG_RESULT_SCHEMA):
        props = schema["properties"]
        assert props["genres"]["items"]["enum"] == tag_vocab.GENRES
        assert props["genres"]["maxItems"] == tag_vocab.MAX_GENRES
        assert props["moods"]["items"]["enum"] == tag_vocab.MOODS
        assert props["moods"]["maxItems"] == tag_vocab.MAX_MOODS
        assert props["otherGenre"]["maxItems"] == 1
        assert "enum" not in props["themes"]["items"]  # themes stay free-form


def test_validator_enforces_the_enum_and_caps() -> None:
    payload = {
        **_valid_tag_payload(),
        "genres": ["Fantasy", "Sci-Fi", "Drama", "Horror", "Mystery", "Romance", "Poetry"],
        "moods": ["Tense", "desperate", "suspenseful", "dark", "eerie", "funny", "cozy", "epic"],
    }
    out = validate_tag_result(payload)
    # mapped, "Drama" dropped, capped at 4 in the order given
    assert out["genres"] == ["Fantasy", "Science Fiction", "Horror", "Mystery"]
    # "desperate" dropped, "suspenseful" folds into "tense", capped at 5
    assert out["moods"] == ["tense", "dark", "unsettling", "funny", "cozy"]
    assert out["otherGenre"] == ["Poetry"]  # an off-list genre isn't lost


def test_validator_checks_other_genre_against_the_map() -> None:
    def other(values: list[str]) -> dict:
        return validate_tag_result({**_valid_tag_payload(), "otherGenre": values})

    assert other(["High Fantasy"])["genres"] == ["Fantasy", "Epic Fantasy"]
    assert other(["High Fantasy"])["otherGenre"] == []
    assert other(["Drama"])["otherGenre"] == []
    assert other(["Epistolary Fiction", "Poetry"])["otherGenre"] == ["Epistolary Fiction"]
    assert other(["Solarpunk"])["otherGenre"] == ["Solarpunk"]


def test_chunk_validator_enforces_the_vocab_too() -> None:
    out = validate_chunk_result(
        {"chunkSummary": "x", "genres": ["Sci-Fi"], "moods": ["relieved", "Eerie"]}
    )
    assert out["genres"] == ["Science Fiction"]
    assert out["moods"] == ["unsettling"]
    assert out["otherGenre"] == []


def test_prompts_carry_the_vocab_and_the_overuse_guidance() -> None:
    from types import SimpleNamespace

    from app.services.llm_tagging_service import (
        _GENRE_RULE,
        _MOOD_RULE,
        _OTHER_GENRE_RULE,
        _SCHEMA_INSTRUCTIONS,
        _build_map_prompt,
    )

    map_prompt = _build_map_prompt(SimpleNamespace(canonical_title="T", author=None), "text", 0, 1)
    for rule in (_GENRE_RULE, _OTHER_GENRE_RULE, _MOOD_RULE):
        assert rule in _SCHEMA_INSTRUCTIONS
        assert rule in map_prompt
    for word in ("Mystery", "Adventure", "Thriller", "Dystopian", "most defining first"):
        assert word in _GENRE_RULE
    for word in ("tense", "reflective", "overall tone", "most defining first"):
        assert word in _MOOD_RULE


async def test_a_book_mid_mapping_at_deploy_reduces_with_free_form_chunks(monkeypatch) -> None:
    """Chunks mapped before the enum shipped keep their free-form values;
    the reduce step still has to work, and sees them mapped."""
    from types import SimpleNamespace

    from app.services import llm_tagging_service as svc

    async def two_docs(data, settings):
        return ["Some text."]

    monkeypatch.setattr(svc, "_extract_documents", two_docs)
    old_chunk = {
        "chunkSummary": "old",
        "genres": ["Sci-Fi", "Drama", "Epistolary Fiction"],
        "moods": ["desperate", "Suspenseful"],
        "themes": ["first contact gone wrong"],
        "representation": [],
        "contentWarnings": [],
    }
    book = SimpleNamespace(
        canonical_title="T",
        author=None,
        llm_tags_json={
            "full": {
                "status": "reducing",
                "chunksTotal": 1,
                "chunksDone": 1,
                "chunkResults": [old_chunk],
            }
        },
    )
    prompts: list[str] = []

    class _Client:
        async def generate_json(self, *, system, prompt, schema=None):
            prompts.append(prompt)
            return {**_valid_tag_payload(), "genres": ["Science Fiction"], "moods": ["tense"]}

    await svc._run_full_step(_Client(), book, b"", get_settings())
    full = book.llm_tags_json["full"]
    assert full["status"] == "done"
    assert full["genresCanonical"] == ["Science Fiction"]
    assert full["moodsCanonical"] == ["tense"]
    assert full["otherGenreCanonical"] == []
    evidence = prompts[0].split("(evidence: ")[1]
    assert "Science Fiction, Epistolary Fiction, tense, first contact gone wrong" in evidence
    assert "desperate" not in evidence and "Drama" not in evidence


async def test_tag_vocab_refresh_after_reduce_never_raises(monkeypatch, caplog) -> None:
    from app.services import llm_tagging_service, tag_vocab

    async def boom(session):
        raise RuntimeError("db locked")

    monkeypatch.setattr(tag_vocab, "refresh_tag_vocab", boom)
    await llm_tagging_service._refresh_tag_vocab()
    assert "tag vocab refresh failed" in caplog.text

