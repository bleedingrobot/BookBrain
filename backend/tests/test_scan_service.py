import hashlib

from sqlalchemy import select

from app.core.config import get_settings
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
from app.services.candidate_service import CandidateService
from app.services.identification_service import IdentificationResult
from app.services.scan_service import ScanService
from tests.epub_fixtures import build_epub


class _FakeDriveProvider:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.download_calls: list[str] = []

    def download_file(self, file_id: str) -> bytes:
        self.download_calls.append(file_id)
        return self._content


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
    }


async def test_process_file_creates_file_and_metadata(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub(title="Foo", authors=("Bar",), isbn="9780134685991"))
    raw = {"id": "drive-1", "name": "foo.epub", "parents": ["parent-1"], "size": "100"}
    counts = _counts()

    await service._process_file(db_session, provider, raw, get_settings(), counts)

    assert counts == {
        "new": 1,
        "flagged": 0,
        "duplicate": 0,
        "skipped_existing": 0,
        "skipped_too_large": 0,
        "failed": 0,
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


async def test_process_file_skips_already_known(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-1", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())
    counts = _counts()
    await service._process_file(db_session, provider, raw, get_settings(), counts)

    assert counts["skipped_existing"] == 1
    assert len(provider.download_calls) == 1  # not re-downloaded on the second pass


async def test_process_file_flags_multi_parent_for_review(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-2", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}
    counts = _counts()

    await service._process_file(db_session, provider, raw, get_settings(), counts)

    assert counts["flagged"] == 1
    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"


async def test_process_file_marks_parse_failure(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(b"this is not a zip file")
    raw = {"id": "drive-3", "name": "corrupt.epub", "parents": ["p"], "size": "100"}
    counts = _counts()

    await service._process_file(db_session, provider, raw, get_settings(), counts)

    assert counts["failed"] == 1
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

    await service._process_file(db_session, provider, raw, get_settings(), counts)

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


async def test_process_file_low_confidence_marks_unidentified(db_session) -> None:
    service = _no_network_scan_service(_identification_result(50))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-6", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "unidentified"
    assert file_row.status_reason is None
    assert file_row.book_id is not None


async def test_process_file_medium_confidence_marks_review(db_session) -> None:
    service = _no_network_scan_service(_identification_result(75))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-7", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "low_confidence"


async def test_process_file_multi_parent_overrides_confidence_routing(db_session) -> None:
    service = _no_network_scan_service(_identification_result(95))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-8", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.status.value == "review"
    assert file_row.status_reason.value == "multi_parent"
    # identification still ran and resolved a book, even though the file is
    # blocked from auto-organizing by the structural issue
    assert file_row.book_id is not None


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

    await service._process_file(db_session, provider, raw, settings, counts)

    assert counts["skipped_too_large"] == 1
    assert provider.download_calls == []
    assert (await db_session.execute(select(File))).scalar_one_or_none() is None


async def test_process_file_creates_review_row_for_medium_confidence(db_session) -> None:
    service = _no_network_scan_service(_identification_result(75))
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-9", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    review = (await db_session.execute(select(Review))).scalar_one()
    assert review.status.value == "pending"
    assert review.proposed_json["computed_confidence"] == 75
    assert review.proposed_json["title"] == "Some Title"


async def test_process_file_creates_review_row_for_multi_parent(db_session) -> None:
    service = _no_network_scan_service()
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-10", "name": "foo.epub", "parents": ["p1", "p2"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    review = (await db_session.execute(select(Review))).scalar_one()
    assert review.status.value == "pending"


async def test_process_file_no_review_row_for_high_confidence(db_session) -> None:
    service = _no_network_scan_service()  # default fake result: confidence 95
    provider = _FakeDriveProvider(build_epub())
    raw = {"id": "drive-11", "name": "foo.epub", "parents": ["p"], "size": "100"}

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

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

    await service._process_file(db_session, provider, raw, get_settings(), counts)

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

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

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
        db_session, provider, {"id": "drive-20", "name": "a.epub", "parents": ["p"], "size": "100"},
        get_settings(), first_counts,
    )
    assert first_counts["new"] == 1

    primary = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-20"))
    ).scalar_one()

    second_counts = _counts()
    await service._process_file(
        db_session, provider, {"id": "drive-21", "name": "b.epub", "parents": ["p"], "size": "100"},
        get_settings(), second_counts,
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

    await service._process_file(db_session, provider, raw, get_settings(), _counts())

    file_row = (await db_session.execute(select(File))).scalar_one()
    assert file_row.quality_score is not None
    assert 0 <= file_row.quality_score <= 100
