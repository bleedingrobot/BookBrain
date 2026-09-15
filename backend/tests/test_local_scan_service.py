from pathlib import Path

from sqlalchemy import select

from app.core.settings_keys import TORRENTS_AUTOMATCH_ENABLED
from app.data.models import LocalFile, LocalFileStatus
from app.data.repositories.settings_repository import SettingsRepository
from app.services import acquisition_service, local_scan_service
from tests.epub_fixtures import build_epub


class _FakeProvider:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.uploads: list[tuple[str, bytes, str]] = []
        self._fail_for = fail_for or set()

    def upload_new_file(self, *, name: str, data: bytes, parent_id: str, mime_type: str) -> dict:
        if name in self._fail_for:
            raise RuntimeError("simulated upload failure")
        self.uploads.append((name, data, parent_id))
        return {"id": f"drive-{name}", "name": name, "parents": [parent_id]}


def _write(root: Path, relative: str, content: bytes = b"data") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def test_scan_local_folder_discovers_supported_extensions(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    _write(tmp_path, "book.kpub")
    _write(tmp_path, "book.mobi")
    _write(tmp_path, "book.rtf")
    _write(tmp_path, "book.txt")
    _write(tmp_path, "comic.cbz")
    _write(tmp_path, "cover.jpg")

    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert {r.filename for r in rows} == {
        "book.epub",
        "book.kpub",
        "book.mobi",
        "book.rtf",
        "book.txt",
        "comic.cbz",
    }
    assert all(r.status == LocalFileStatus.pending for r in rows)


async def test_scan_local_folder_recurses_into_subfolders(db_session, tmp_path) -> None:
    _write(tmp_path, "top.epub")
    _write(tmp_path, "Series/Author/nested.epub")

    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert {r.filename for r in rows} == {"top.epub", "nested.epub"}


async def test_scan_local_folder_does_not_reoffer_already_seen_paths(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")

    first = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    assert len(first) == 1

    second = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    assert len(second) == 1
    assert second[0].id == first[0].id

    all_rows = (await db_session.execute(select(LocalFile))).scalars().all()
    assert len(all_rows) == 1  # no duplicate row created


async def test_scan_local_folder_missing_directory_returns_existing_pending(db_session) -> None:
    rows = await local_scan_service.scan_local_folder(db_session, "Z:\\does-not-exist")

    assert rows == []


async def test_copy_to_drive_uploads_and_marks_copied(db_session, tmp_path) -> None:
    epub_bytes = build_epub()
    path = _write(tmp_path, "book.epub", epub_bytes)
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    provider = _FakeProvider()

    result = await local_scan_service.copy_to_drive(db_session, [rows[0].id], provider, "inbox-id")

    assert result == {"copied": 1, "failed": 0}
    assert provider.uploads == [("book.epub", epub_bytes, "inbox-id")]

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.status == LocalFileStatus.copied
    assert Path(path).exists()  # local file itself is untouched


async def test_copy_to_drive_counts_failures_without_crashing(db_session, tmp_path) -> None:
    _write(tmp_path, "good.epub", build_epub())
    _write(tmp_path, "bad.epub", build_epub())
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    provider = _FakeProvider(fail_for={"bad.epub"})

    result = await local_scan_service.copy_to_drive(
        db_session, [r.id for r in rows], provider, "inbox-id"
    )

    assert result == {"copied": 1, "failed": 1}
    statuses = {r.filename: r.status for r in (await db_session.execute(select(LocalFile))).scalars().all()}
    assert statuses["good.epub"] == LocalFileStatus.copied
    assert statuses["bad.epub"] == LocalFileStatus.pending  # left alone, retryable


async def test_copy_to_drive_rejects_malformed_epub(db_session, tmp_path) -> None:
    """Regression test for the inbox_upload_service unification: local_scan_service
    used to upload a file's raw bytes with no format/zip-sanity validation at
    all — now it shares acquire_service's checks via inbox_upload_service, so a
    file that merely has an .epub extension but isn't really one is rejected
    the same way an OpenBooks download would be."""
    _write(tmp_path, "not-really-an-epub.epub", b"this is not a zip file")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    provider = _FakeProvider()

    result = await local_scan_service.copy_to_drive(db_session, [rows[0].id], provider, "inbox-id")

    assert result == {"copied": 0, "failed": 1}
    assert provider.uploads == []
    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.status == LocalFileStatus.pending  # left alone, retryable


async def test_dismiss_marks_dismissed_and_excludes_from_pending(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    count = await local_scan_service.dismiss(db_session, [rows[0].id])

    assert count == 1
    remaining = await local_scan_service.list_pending(db_session)
    assert remaining == []


async def test_dismissed_files_are_not_reoffered_on_rescan(db_session, tmp_path) -> None:
    _write(tmp_path, "book.epub")
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))
    await local_scan_service.dismiss(db_session, [rows[0].id])

    second = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    assert second == []


# --- match_against_wishlist (ROADMAP.md "close the acquisition loop") ---


def _targets(*, title="The Final Empire", author="Brandon Sanderson", request_id="r1", source="wishlist"):
    return [{"request_id": request_id, "source": source, "title": title, "author": author, "isbn13": None}]


def _plausibly_sized_epub() -> bytes:
    # score_candidate penalizes anything under 40KB as a likely stub/sample —
    # pad the fixture well past that so a real match scores as a real match.
    return build_epub(chapter_text="<html><body>" + ("x" * 60_000) + "</body></html>")


async def test_match_against_wishlist_flags_a_strong_match_without_automatching(
    db_session, tmp_path, monkeypatch
) -> None:
    _write(tmp_path, "Brandon Sanderson - The Final Empire.epub", _plausibly_sized_epub())
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    async def fake_gather(provider, folder):
        return _targets()

    monkeypatch.setattr(acquisition_service, "gather_acquisition_targets", fake_gather)
    # TORRENTS_AUTOMATCH_ENABLED left off (the default) — a match should only
    # be flagged, never auto-uploaded.
    provider = _FakeProvider()

    await local_scan_service.match_against_wishlist(db_session, rows, provider, "inbox-id", "lib-id")

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.matched_request_id == "r1"
    assert updated.matched_title == "The Final Empire"
    assert updated.matched_score >= acquisition_service._STRONG_SCORE
    assert updated.status == LocalFileStatus.pending  # left for a human to confirm
    assert provider.uploads == []


async def test_match_against_wishlist_auto_uploads_a_strong_match_when_enabled(
    db_session, tmp_path, monkeypatch
) -> None:
    _write(tmp_path, "Brandon Sanderson - The Final Empire.epub", _plausibly_sized_epub())
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    async def fake_gather(provider, folder):
        return _targets()

    monkeypatch.setattr(acquisition_service, "gather_acquisition_targets", fake_gather)
    await SettingsRepository(db_session).set(TORRENTS_AUTOMATCH_ENABLED, "true")

    marked = {}

    async def fake_mark(provider, folder, request_id):
        marked["request_id"] = request_id
        return True

    monkeypatch.setattr(acquisition_service, "mark_wishlist_sourced", fake_mark)
    provider = _FakeProvider()

    await local_scan_service.match_against_wishlist(db_session, rows, provider, "inbox-id", "lib-id")

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.status == LocalFileStatus.copied
    assert len(provider.uploads) == 1
    assert provider.uploads[0][2] == "inbox-id"  # went to the inbox, not the library folder
    assert marked["request_id"] == "r1"


async def test_match_against_wishlist_ignores_a_weak_match(db_session, tmp_path, monkeypatch) -> None:
    _write(tmp_path, "Totally Unrelated Filename.epub", build_epub())
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    async def fake_gather(provider, folder):
        return _targets()

    monkeypatch.setattr(acquisition_service, "gather_acquisition_targets", fake_gather)

    await local_scan_service.match_against_wishlist(
        db_session, rows, _FakeProvider(), "inbox-id", "lib-id"
    )

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.matched_request_id is None
    assert updated.matched_score is None


async def test_match_against_wishlist_skips_an_unparsable_filename(db_session, tmp_path, monkeypatch) -> None:
    _write(tmp_path, "1984.epub", build_epub())  # bare title, low filename-parser confidence
    rows = await local_scan_service.scan_local_folder(db_session, str(tmp_path))

    called = False

    async def fake_gather(provider, folder):
        nonlocal called
        called = True
        return _targets(title="1984", author=None)

    monkeypatch.setattr(acquisition_service, "gather_acquisition_targets", fake_gather)

    await local_scan_service.match_against_wishlist(
        db_session, rows, _FakeProvider(), "inbox-id", "lib-id"
    )

    updated = await db_session.get(LocalFile, rows[0].id)
    assert updated.matched_request_id is None
    # gather is still called (it's fetched once up front, before per-row
    # filtering) — what matters is the unusable guess never gets scored.
    assert called is True
