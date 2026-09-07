import pytest

from app.data.models import Author, Book, File, FileStatus
from app.services import description_service
from app.services.description_service import (
    backfill_descriptions,
    estimate_description_backfill,
)


@pytest.fixture(autouse=True)
def _route_db_to_test_session(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(description_service, "async_session_factory", lambda: _CM())
    # One shared test session — serialise the workers so concurrent coroutines
    # don't use it at once.
    monkeypatch.setattr(description_service, "_CONCURRENCY", 1)


class _NoCandidates:
    async def generate_candidates(self, **_kw):
        return []


class _CountingAI:
    def __init__(self) -> None:
        self.calls = 0

    async def describe(self, title, author):
        self.calls += 1
        return f"A blurb for {title}."


async def _seed_books(db_session, n: int) -> None:
    author = Author(name="A")
    db_session.add(author)
    await db_session.flush()
    for i in range(n):
        book = Book(canonical_title=f"Book {i}", author_id=author.id)
        db_session.add(book)
        await db_session.flush()
        db_session.add(
            File(
                drive_file_id=f"d{i}",
                filename=f"b{i}.epub",
                sha256=f"s{i}",
                size_bytes=1,
                status=FileStatus.organised,
                book_id=book.id,
            )
        )
    await db_session.commit()


async def test_estimate_makes_no_ai_calls_and_counts_blanks(db_session, monkeypatch) -> None:
    await _seed_books(db_session, 5)

    def _boom(*_a, **_k):
        raise AssertionError("estimate must not construct an AI client")

    monkeypatch.setattr(description_service, "AnthropicIdentificationClient", _boom)

    est = await estimate_description_backfill()

    assert est.books_missing == 5
    assert est.will_process == 5  # below the default cap
    assert est.cap >= 5
    assert est.estimated_cost_usd > 0


async def test_ai_backfill_respects_the_cap_and_a_rerun_continues(db_session, monkeypatch) -> None:
    await _seed_books(db_session, 5)
    ai = _CountingAI()
    monkeypatch.setattr(description_service, "default_candidate_service", lambda: _NoCandidates())
    monkeypatch.setattr(description_service, "AnthropicIdentificationClient", lambda: ai)

    first = await backfill_descriptions(use_ai=True, ai_cap=2)
    assert ai.calls == 2
    assert first["from_ai"] == 2
    assert first["remaining"] == 3  # 3 deferred by the cap

    second = await backfill_descriptions(use_ai=True, ai_cap=2)
    assert ai.calls == 4  # continued where it left off
    assert second["from_ai"] == 2
    assert second["remaining"] == 1
