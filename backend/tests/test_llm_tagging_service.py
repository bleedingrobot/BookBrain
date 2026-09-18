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


# --------------------------------------------------------------------------
# response validation
# --------------------------------------------------------------------------


def _valid_tag_payload() -> dict:
    return {
        "ageRating": "Teen",
        "genres": ["Fantasy"],
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
    payload = {**_valid_tag_payload(), "themes": themes, "genres": ["Survival"]}
    assert validate_tag_result(payload)["themes"] == ["survival against supernatural threats", "revenge"]
    assert validate_tag_result(payload)["genres"] == ["Survival"]  # themes only
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
