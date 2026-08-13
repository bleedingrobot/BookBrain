import asyncio
import hashlib

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.settings_keys import ORGANIZE_DRY_RUN
from app.data.db import Base
from app.data.repositories.settings_repository import SettingsRepository
from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    File,
    FileStatus,
    LibraryRule,
    MetadataSource,
    Review,
    ReviewStatus,
    RuleType,
)
from app.providers.metadata.types import MetadataCandidate
from app.schemas.drive import FolderConfig
from app.services.candidate_service import CandidateService
from app.services.identification_service import IdentificationResult
from app.services.scan_service import ScanService
from tests.epub_fixtures import build_epub


@pytest.fixture(autouse=True)
def _route_scan_and_organize_db_to_test_session(db_session, monkeypatch):
    """_process_file and organize_eligible_files each open their own
    short-lived sessions (a shared AsyncSession isn't safe across
    concurrently-processing files) instead of taking one in as a
    parameter — route those internal async_session_factory() calls to the
    test's isolated db_session for every test in this file, so nothing
    here ever touches the real configured database. Safe to share one
    session object across a single test's sequential internal session
    blocks (no genuine concurrency within one _process_file call); tests
    that exercise real cross-file concurrency use their own file-backed
    DB instead (see test_process_files_concurrently_*)."""
    import app.services.organize_service as organize_module
    import app.services.scan_service as scan_module

    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(scan_module, "async_session_factory", lambda: _CM())
    monkeypatch.setattr(organize_module, "async_session_factory", lambda: _CM())


class _FakeDriveProvider:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.download_calls: list[str] = []
        self.trash_calls: list[str] = []
        self.update_content_calls: list[tuple[str, str, bytes]] = []

    def download_file(self, file_id: str) -> bytes:
        self.download_calls.append(file_id)
        return self._content

    def trash_file(self, file_id: str) -> dict:
        self.trash_calls.append(file_id)
        return {"id": file_id, "trashed": True}

    def update_file_content(self, file_id: str, *, new_name: str, data: bytes) -> dict:
        self.update_content_calls.append((file_id, new_name, data))
        return {"id": file_id, "name": new_name}


class _FakeIdentificationService:
    """Bypasses real fast-path/AI/confidence logic (covered by
    test_identification_service.py and test_confidence_service.py) so these
    tests focus on scan_service's own orchestration: book resolution and
    status routing from whatever confidence identification returns."""

    def __init__(self, result: IdentificationResult | None = None) -> None:
        self._result = result

    async def identify(self, *, filename, evidence, candidates) -> IdentificationResult:
        if self._result is not None:
            return self._result
        return IdentificationResult(
            title=evidence.title or filename,
            author=evidence.authors[0] if evidence.authors else None,
            series=evidence.series,
            series_number=evidence.series_number,
            computed_confidence=95,
            ai_reported_confidence=None,
            needs_human_review=False,
            reasoning_summary="test double",
            model="fake",
            prompt_hash="fake-prompt-hash",
            evidence_hash="fake-evidence-hash",
            raw_response={},
        )


def _no_network_scan_service(identification_result: IdentificationResult | None = None) -> ScanService:
    return ScanService(
        candidate_service=CandidateService(providers=[]),
        identification_service=_FakeIdentificationService(identification_result),
    )


def _counts() -> dict[str, int]:
    return {
        "new": 0,
        "flagged": 0,
        "duplicate": 0,
        "skipped_existing": 0,
        "skipped_too_large": 0,
        "failed": 0,
        "removed_non_ebook": 0,
        "converted": 0,
    }


async def test_process_file_safely_does_not_abort_batch_on_unexpected_error(monkeypatch) -> None:
    # Regression: an unhandled exception mid-file (a UNIQUE-constraint race
    # from an overlapping job, or anything else unexpected) used to crash
    # the whole run_scan/run_rebuild loop, leaving the job stuck at
    # "running" forever with nothing surfaced to the user.
    import app.services.scan_service as scan_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(scan_module, "resolve_book", _boom)

    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-boom", "name": "boom.epub", "parents": ["p"], "size": "100"}
    counts = _counts()
    failures: list = []

    await service._process_file_safely(provider, raw, get_settings(), counts, failures, asyncio.Lock())

    assert counts["failed"] == 1
    assert failures[0].filename == "boom.epub"
    assert "simulated unexpected failure" in failures[0].reason


async def test_process_file_creates_file_and_metadata(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub(title="Foo", authors=("Bar",), isbn="9780134685991"))
    raw = {"id": "drive-1", "name": "foo.epub", "parents": ["parent-1"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts == {
        "new": 1,
        "flagged": 0,
        "duplicate": 0,
        "skipped_existing": 0,
        "skipped_too_large": 0,
        "failed": 0,
        "removed_non_ebook": 0,
        "converted": 0,
    }

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.drive_file_id == "drive-1"
    assert file_row.drive_parent_id == "parent-1"
    assert file_row.status.value == "inbox"
    assert file_row.status_reason is None
    assert file_row.book_id is not None

    field_names = {
        s.field_name for s in (await db_session.execute(select(MetadataSource))).scalars().all()
    }
    assert {"filename", "title", "authors", "isbn13"} <= field_names

    decision = (await db_session.execute(select(AIDecision))).scalar_one()
    assert decision.computed_confidence == 95
    assert decision.file_id == file_row.id


async def test_process_file_accepts_kpub_extension(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub(title="Foo", authors=("Bar",), isbn="9780134685991"))
    raw = {"id": "drive-kpub", "name": "foo.kpub", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["new"] == 1
    assert provider.trash_calls == []
    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.drive_file_id == "drive-kpub"


async def test_process_file_trashes_non_ebook_files_instead_of_processing(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"not a book")
    raw = {"id": "drive-junk", "name": "cover.jpg", "parents": ["p"], "size": "50"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["removed_non_ebook"] == 1
    assert counts["new"] == 0
    assert provider.trash_calls == ["drive-junk"]
    assert provider.download_calls == []  # never even downloaded
    assert (await db_session.execute(select(File))).scalars().all() == []


async def test_process_file_converts_mobi_before_processing(db_session, monkeypatch) -> None:
    import app.services.scan_service as scan_module

    converted_bytes = build_epub(title="Converted Book", authors=("Someone",))

    async def fake_convert(data, *, source_filename, binary, timeout_seconds):
        assert source_filename == "book.mobi"
        return converted_bytes

    monkeypatch.setattr(scan_module, "convert_to_epub", fake_convert)

    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"fake mobi bytes")
    raw = {"id": "drive-mobi", "name": "book.mobi", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["converted"] == 1
    assert counts["new"] == 1
    assert provider.trash_calls == []
    assert provider.download_calls == ["drive-mobi"]  # only the pre-conversion download — no redundant re-fetch
    assert provider.update_content_calls == [("drive-mobi", "book.epub", converted_bytes)]

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.drive_file_id == "drive-mobi"
    assert file_row.filename == "book.epub"
    assert file_row.status.value == "inbox"


async def test_process_file_converts_rtf_before_processing(db_session, monkeypatch) -> None:
    import app.services.scan_service as scan_module

    converted_bytes = build_epub()

    async def fake_convert(data, *, source_filename, binary, timeout_seconds):
        return converted_bytes

    monkeypatch.setattr(scan_module, "convert_to_epub", fake_convert)

    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"fake rtf bytes")
    raw = {"id": "drive-rtf", "name": "notes.RTF", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["converted"] == 1
    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.filename == "notes.epub"


async def test_process_file_conversion_failure_counts_as_failed_and_leaves_file_alone(
    db_session, monkeypatch
) -> None:
    import app.services.scan_service as scan_module

    async def fake_convert(*args, **kwargs):
        raise RuntimeError("simulated conversion failure")

    monkeypatch.setattr(scan_module, "convert_to_epub", fake_convert)

    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"fake mobi bytes")
    raw = {"id": "drive-broken-mobi", "name": "broken.mobi", "parents": ["p"], "size": "100"}
    counts = _counts()
    failures: list = []

    await service._process_file(provider, raw, get_settings(), counts, failures, asyncio.Lock())

    assert counts["failed"] == 1
    assert counts["converted"] == 0
    assert provider.trash_calls == []  # left alone, not trashed — retryable
    assert provider.update_content_calls == []
    assert (await db_session.execute(select(File))).scalars().all() == []
    assert failures[0].filename == "broken.mobi"
    assert "simulated conversion failure" in failures[0].reason


async def test_process_file_download_failure_counts_as_failed(db_session) -> None:
    class _FailingDownloadProvider:
        def download_file(self, file_id: str) -> bytes:
            raise RuntimeError("simulated download failure")

    service = _no_network_scan_service()
    provider = _FailingDownloadProvider()
    raw = {"id": "drive-unreachable", "name": "unreachable.epub", "parents": ["p"], "size": "100"}
    counts = _counts()
    failures: list = []

    await service._process_file(provider, raw, get_settings(), counts, failures, asyncio.Lock())

    assert counts["failed"] == 1
    assert (await db_session.execute(select(File))).scalars().all() == []
    assert failures[0].filename == "unreachable.epub"
    assert "simulated download failure" in failures[0].reason


async def test_process_file_skips_already_known(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-1", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())
    counts = _counts()
    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["skipped_existing"] == 1
    assert len(provider.download_calls) == 1  # not re-downloaded on the second pass


async def test_process_file_flags_multi_parent_for_review(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-2", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["flagged"] == 1
    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"


async def test_process_file_marks_parse_failure(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"this is not a zip file")
    raw = {"id": "drive-3", "name": "corrupt.epub", "parents": ["p"], "size": "100"}
    counts = _counts()
    failures: list = []

    await service._process_file(provider, raw, get_settings(), counts, failures, asyncio.Lock())

    assert counts["failed"] == 1
    assert failures[0].filename == "corrupt.epub"
    assert "not a valid zip archive" in failures[0].reason
    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "unidentified"
    assert file_row.status_reason.value == "parse_failed"
    # sha256/size are still populated — we already had the bytes before parsing failed
    assert file_row.sha256
    assert file_row.size_bytes == len(b"this is not a zip file")


class _FakeMetadataProvider:
    name = "fake_provider"

    def __init__(self, candidates: list[MetadataCandidate]) -> None:
        self._candidates = candidates

    async def search_by_isbn(self, isbn: str) -> list[MetadataCandidate]:
        return self._candidates

    async def search_by_title_author(
        self, title: str, author: str | None
    ) -> list[MetadataCandidate]:
        return self._candidates


async def test_process_file_persists_candidates(db_session) -> None:
    candidate = MetadataCandidate(
        title="Foo", authors=["Bar"], isbn13="9780134685991", source="fake_provider"
    )
    service = ScanService(
        candidate_service=CandidateService(providers=[_FakeMetadataProvider([candidate])])
    )
    provider = _FakeDriveProvider(build_epub(title="Foo", authors=("Bar",), isbn="9780134685991"))
    raw = {"id": "drive-5", "name": "foo.epub", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    stored = (await db_session.execute(select(BookCandidate))).scalars().all()
    assert len(stored) == 1
    assert stored[0].title == "Foo"
    assert stored[0].author == "Bar"
    assert stored[0].source == "fake_provider"


def _identification_result(confidence: int) -> IdentificationResult:
    return IdentificationResult(
        title="Some Title",
        author="Some Author",
        series=None,
        series_number=None,
        computed_confidence=confidence,
        ai_reported_confidence=None,
        needs_human_review=confidence < 85,
        reasoning_summary="test",
        model="fake",
        prompt_hash="h",
        evidence_hash="h",
        raw_response={},
    )


async def test_process_file_low_confidence_sends_to_review_not_unidentified(db_session) -> None:
    # A low-confidence AI call (thin evidence: no ISBN, one provider) can
    # still be a correct identification — it must land somewhere actionable
    # (the review queue), never in the unreviewable `unidentified` dead end.
    service = _no_network_scan_service(_identification_result(50))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-6", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "low_confidence"
    assert file_row.book_id is not None

    review = (await db_session.execute(select(Review))).scalar_one()
    assert review.file_id == file_row.id
    assert review.status.value == "pending"


async def test_process_file_medium_confidence_marks_review(db_session) -> None:
    service = _no_network_scan_service(_identification_result(75))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-7", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "low_confidence"


async def test_process_file_multi_parent_overrides_confidence_routing(db_session) -> None:
    service = _no_network_scan_service(_identification_result(95))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-8", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"
    # identification still ran and resolved a book, even though the file is
    # blocked from auto-organizing by the structural issue
    assert file_row.book_id is not None


async def test_process_file_already_organised_skips_review_even_at_low_confidence(db_session) -> None:
    # Rebuild mode: a file found sitting in the library folder is trusted
    # as already-placed, regardless of what its confidence score is — it
    # must never be routed to the review queue.
    service = _no_network_scan_service(_identification_result(20))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-9", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(
        provider, raw, get_settings(), _counts(), [], asyncio.Lock(), already_organised=True
    )

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "organised"
    assert file_row.status_reason is None
    assert (await db_session.execute(select(Review))).scalar_one_or_none() is None


async def test_process_file_already_organised_still_flags_structural_issues(db_session) -> None:
    service = _no_network_scan_service(_identification_result(95))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-10", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}

    await service._process_file(
        provider, raw, get_settings(), _counts(), [], asyncio.Lock(), already_organised=True
    )

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"


async def test_run_rebuild_walks_library_tree_and_marks_organised(db_session, monkeypatch) -> None:
    epub_bytes = build_epub(title="Foo", authors=("Bar",))

    class _FakeRecursiveProvider:
        def __init__(self) -> None:
            self.download_calls: list[str] = []

        def list_epub_files_recursive(self, root_folder_id: str) -> list[dict]:
            return [{"id": "drive-lib-1", "name": "foo.epub", "parents": ["author-folder"], "size": "10"}]

        def download_file(self, file_id: str) -> bytes:
            self.download_calls.append(file_id)
            return epub_bytes

    fake_provider = _FakeRecursiveProvider()
    monkeypatch.setattr(
        "app.services.scan_service.DriveProvider", lambda _service: fake_provider
    )
    monkeypatch.setattr("app.services.scan_service.build_drive_service", lambda _creds: object())

    service = _no_network_scan_service(_identification_result(95))
    job = service.create_job()

    await service.run_rebuild(job.job_id, creds=object(), library_root_folder_id="lib-root")

    status = service.get_status(job.job_id)
    assert status.status.value == "done"
    assert "1 rebuilt" in status.detail

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "organised"
    assert file_row.drive_file_id == "drive-lib-1"


async def test_process_file_skips_declared_oversize_without_downloading(db_session) -> None:
    service = _no_network_scan_service()
    settings = get_settings()
    provider = _FakeDriveProvider(b"unused")
    raw = {
        "id": "drive-4",
        "name": "huge.epub",
        "parents": ["p"],
        "size": str(settings.epub_max_total_bytes + 1),
    }
    counts = _counts()

    await service._process_file(provider, raw, settings, counts, [], asyncio.Lock())

    assert counts["skipped_too_large"] == 1
    assert provider.download_calls == []
    assert (await db_session.execute(select(File))).scalar_one_or_none() is None


async def test_process_file_creates_review_row_for_medium_confidence(db_session) -> None:
    service = _no_network_scan_service(_identification_result(75))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-9", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    review = (await db_session.execute(select(Review))).scalar_one()
    assert review.status.value == "pending"
    assert review.proposed_json["computed_confidence"] == 75
    assert review.proposed_json["title"] == "Some Title"


async def test_process_file_creates_review_row_for_multi_parent(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-10", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    review = (await db_session.execute(select(Review))).scalar_one()
    assert review.status.value == "pending"


async def test_process_file_no_review_row_for_high_confidence(db_session) -> None:
    service = _no_network_scan_service()  # default fake result: confidence 95
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-11", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    assert (await db_session.execute(select(Review))).scalar_one_or_none() is None


async def test_process_file_duplicate_of_corrected_content_inherits_correction(db_session) -> None:
    epub_bytes = build_epub()
    sha256 = hashlib.sha256(epub_bytes).hexdigest()

    # seed a prior corrected review for the exact same content hash, under a
    # different (already-processed) file row
    other_file = File(
        drive_file_id="already-known",
        drive_parent_id="p",
        filename="other.epub",
        sha256=sha256,
        size_bytes=len(epub_bytes),
        status=FileStatus.unidentified,
    )
    db_session.add(other_file)
    await db_session.flush()
    db_session.add(
        Review(
            file_id=other_file.id,
            status=ReviewStatus.corrected,
            proposed_json={},
            correction_json={"title": "Sticky Title", "author": "Sticky Author"},
        )
    )
    await db_session.commit()

    class _ExplodingCandidateService(CandidateService):
        def __init__(self) -> None:
            super().__init__(providers=[])

        async def generate_candidates(self, **kwargs):
            raise AssertionError("candidate generation should have been skipped")

    service = ScanService(
        candidate_service=_ExplodingCandidateService(),
        identification_service=None,
    )
    provider = _FakeDriveProvider(epub_bytes)
    raw = {"id": "drive-12", "name": "foo.epub", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["duplicate"] == 1

    new_file = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-12"))
    ).scalar_one()
    # a second copy of already-corrected content is still a duplicate — but
    # one that correctly inherits the human-verified identity, not the
    # (still-unidentified) primary's
    assert new_file.status.value == "duplicate"
    assert (
        await db_session.execute(select(AIDecision).where(AIDecision.file_id == new_file.id))
    ).scalar_one_or_none() is None

    book = (await db_session.execute(select(Book).where(Book.id == new_file.book_id))).scalar_one()
    assert book.canonical_title == "Sticky Title"


async def test_process_file_duplicate_of_rejected_content_is_flagged(db_session) -> None:
    epub_bytes = build_epub()
    sha256 = hashlib.sha256(epub_bytes).hexdigest()

    rejected_file = File(
        drive_file_id="already-rejected",
        drive_parent_id="p",
        filename="rejected.epub",
        sha256=sha256,
        size_bytes=len(epub_bytes),
        status=FileStatus.rejected,
    )
    db_session.add(rejected_file)
    await db_session.commit()

    service = _no_network_scan_service()
    provider = _FakeDriveProvider(epub_bytes)
    raw = {"id": "drive-13", "name": "foo.epub", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(provider, raw, get_settings(), counts, [], asyncio.Lock())

    assert counts["duplicate"] == 1
    new_file = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-13"))
    ).scalar_one()
    assert new_file.status.value == "duplicate"
    assert new_file.status_reason.value == "previously_rejected"


async def test_process_file_library_rule_skips_candidates_and_ai(db_session) -> None:
    db_session.add(
        LibraryRule(
            rule_type=RuleType.author_alias,
            pattern="Jane Author",
            resolution_json={"author": "Jane A. Author"},
        )
    )
    await db_session.commit()

    class _ExplodingCandidateService(CandidateService):
        def __init__(self) -> None:
            super().__init__(providers=[])

        async def generate_candidates(self, **kwargs):
            raise AssertionError("candidate generation should have been skipped")

    service = ScanService(
        candidate_service=_ExplodingCandidateService(),
        identification_service=None,
    )
    provider = _FakeDriveProvider(build_epub())  # default author is "Jane Author"
    raw = {"id": "drive-13", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    decision = (await db_session.execute(select(AIDecision))).scalar_one()
    assert decision.model == "library_rule"

    file_row = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-13"))
    ).scalar_one()
    book = (await db_session.execute(select(Book).where(Book.id == file_row.book_id))).scalar_one()
    author = (await db_session.execute(select(Author).where(Author.id == book.author_id))).scalar_one()
    assert author.name == "Jane A. Author"


async def test_process_file_detects_plain_duplicate_and_copies_primary(db_session) -> None:
    epub_bytes = build_epub(title="Foo", authors=("Bar",), isbn="9780134685991")
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(epub_bytes)

    first_counts = _counts()
    await service._process_file(
        provider, {"id": "drive-20", "name": "a.epub", "parents": ["p"], "size": "100"},
        get_settings(), first_counts, [], asyncio.Lock(),
    )
    assert first_counts["new"] == 1

    primary = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-20"))
    ).scalar_one()

    second_counts = _counts()
    await service._process_file(
        provider, {"id": "drive-21", "name": "b.epub", "parents": ["p"], "size": "100"},
        get_settings(), second_counts, [], asyncio.Lock(),
    )

    assert second_counts["duplicate"] == 1
    dup = (await db_session.execute(select(File).where(File.drive_file_id == "drive-21"))).scalar_one()
    assert dup.status.value == "duplicate"
    assert dup.book_id == primary.book_id
    assert dup.quality_score == primary.quality_score


async def test_process_file_sets_quality_score_on_successful_parse(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-22", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(provider, raw, get_settings(), _counts(), [], asyncio.Lock())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.quality_score is not None
    assert 0 <= file_row.quality_score <= 100


async def test_process_batch_processes_all_files_concurrently_and_avoids_duplicate_authors(
    tmp_path, monkeypatch
) -> None:
    # _process_batch gives each file its own DB session (needs a real
    # file-backed DB — :memory: is per-connection, so separate sessions
    # wouldn't share data) and runs them concurrently. The regression this
    # guards: three different new books by the same not-yet-seen author,
    # processed at once, must still resolve to ONE Author row — without
    # db_lock serializing the fuzzy find-or-create, each could see "no
    # such author yet" and create its own.
    import app.services.scan_service as scan_module

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scan_concurrency.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    class _CM:
        async def __aenter__(self):
            self._session = session_factory()
            return self._session

        async def __aexit__(self, *args):
            await self._session.close()
            return False

    monkeypatch.setattr(scan_module, "async_session_factory", lambda: _CM())

    # No fixed result — _FakeIdentificationService's default behavior reads
    # title/author from each file's own parsed evidence, so the 6 files
    # correctly resolve to 6 different titles sharing one author, rather
    # than all 6 identifying as the exact same book.
    service = _no_network_scan_service()

    class _FakeMultiFileProvider:
        def __init__(self, files: dict[str, bytes]) -> None:
            self._files = files

        def download_file(self, file_id: str) -> bytes:
            return self._files[file_id]

        def trash_file(self, file_id: str) -> dict:
            raise AssertionError("not expected")

    files = {
        f"drive-{i}": build_epub(title=f"Book {i}", authors=("Shared Author",)) for i in range(6)
    }
    provider = _FakeMultiFileProvider(files)
    raw_files = [
        {"id": file_id, "name": f"{file_id}.epub", "parents": ["p"], "size": "100"}
        for file_id in files
    ]
    counts = _counts()
    failures: list = []

    await service._process_batch(
        None, raw_files, get_settings(), counts, failures, provider_factory=lambda: provider
    )

    assert counts["new"] == 6
    assert failures == []

    async with session_factory() as check:
        authors = (await check.execute(select(Author))).scalars().all()
        assert len(authors) == 1
        assert authors[0].name == "Shared Author"

        book_ids = {f.book_id for f in (await check.execute(select(File))).scalars().all()}
        assert len(book_ids) == 6  # 6 distinct books, correctly not merged into one

    await engine.dispose()


async def test_run_scan_auto_organizes_eligible_files_after_scanning(db_session, monkeypatch) -> None:
    import app.services.organize_service as organize_module
    import app.services.scan_service as scan_module

    await SettingsRepository(db_session).set(ORGANIZE_DRY_RUN, "false")

    epub_bytes = build_epub(title="Foo", authors=("Bar",))

    class _FakeScanProvider:
        def list_files_in_folder(self, folder_id: str) -> list[dict]:
            return [{"id": "drive-scan-1", "name": "foo.epub", "parents": ["inbox"], "size": "10"}]

        def download_file(self, file_id: str) -> bytes:
            return epub_bytes

        def list_folders(self, parent_id: str | None) -> list[dict]:
            return []

        def create_folder(self, name: str, parent_id: str | None = None) -> dict:
            return {"id": f"folder-{name}", "name": name}

        def move_and_rename(self, file_id, *, old_parent_id, new_parent_id, new_name) -> dict:
            return {"id": file_id, "name": new_name, "parents": [new_parent_id]}

    monkeypatch.setattr(scan_module, "DriveProvider", lambda _service: _FakeScanProvider())
    monkeypatch.setattr(scan_module, "build_drive_service", lambda _creds: object())
    # organize_eligible_files now builds its own DriveProvider per file
    # (see organize_service.py — no longer accepts one pre-built provider),
    # via its own imports of DriveProvider/build_drive_service, not
    # scan_module's — so the auto-organize half of this test needs its own
    # patch too, or it tries to build a real Drive client from the fake
    # `creds=object()` below and blows up.
    monkeypatch.setattr(organize_module, "DriveProvider", lambda _service: _FakeScanProvider())
    monkeypatch.setattr(organize_module, "build_drive_service", lambda _creds: object())

    async def fake_get_library_folder_config(_settings_repo):
        return FolderConfig(folder_id="lib-root", folder_name="Library", created_by_app=False)

    monkeypatch.setattr(
        scan_module.DriveService, "get_library_folder_config", fake_get_library_folder_config
    )

    service = _no_network_scan_service(_identification_result(95))
    job = service.create_job()

    await service.run_scan(job.job_id, creds=object(), folder_id="inbox-root")

    status = service.get_status(job.job_id)
    assert status.status.value == "done"
    assert "1 auto-organized" in status.detail

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "organised"
    assert file_row.drive_parent_id == "folder-Some Author"


async def test_run_scan_skips_auto_organize_without_a_library_folder(monkeypatch) -> None:
    import app.services.scan_service as scan_module

    class _FakeScanProvider:
        def list_files_in_folder(self, folder_id: str) -> list[dict]:
            return []

    monkeypatch.setattr(scan_module, "DriveProvider", lambda _service: _FakeScanProvider())
    monkeypatch.setattr(scan_module, "build_drive_service", lambda _creds: object())

    async def fake_get_library_folder_config(_settings_repo):
        return None

    monkeypatch.setattr(
        scan_module.DriveService, "get_library_folder_config", fake_get_library_folder_config
    )

    service = _no_network_scan_service()
    job = service.create_job()

    await service.run_scan(job.job_id, creds=object(), folder_id="inbox-root")

    status = service.get_status(job.job_id)
    assert status.status.value == "done"
    assert "0 auto-organized" in status.detail
