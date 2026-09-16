import pytest

from app.data.models import File, FileStatus
from app.services import scan_service
from app.services.scan_service import estimate_rebuild


@pytest.fixture(autouse=True)
def _route_db(db_session, monkeypatch):
    class _CM:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(scan_service, "async_session_factory", lambda: _CM())


class _FakeProvider:
    def list_epub_files_recursive(self, _root):
        return [{"id": "known-1"}, {"id": "known-2"}, {"id": "new-1"}, {"id": "new-2"}, {"id": "new-3"}]


async def test_estimate_rebuild_counts_unknown_files(db_session, monkeypatch) -> None:
    db_session.add(
        File(
            drive_file_id="known-1",
            filename="k1.epub",
            sha256="s1",
            size_bytes=1,
            status=FileStatus.organised,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(scan_service, "build_drive_service", lambda _c: object())
    monkeypatch.setattr(scan_service, "DriveProvider", lambda _s: _FakeProvider())

    est = await estimate_rebuild(object(), "lib-root")

    assert est.estimated is True
    assert est.files_to_identify == 4  # known-2 isn't in `files` yet either
    assert est.estimated_cost_usd > 0


async def test_estimate_rebuild_degrades_without_creds() -> None:
    est = await estimate_rebuild(None, "lib-root")
    assert est.estimated is False
    assert est.files_to_identify == 0


async def test_estimate_rebuild_degrades_on_listing_failure(monkeypatch) -> None:
    class _Boom:
        def list_epub_files_recursive(self, _root):
            raise RuntimeError("drive down")

    monkeypatch.setattr(scan_service, "build_drive_service", lambda _c: object())
    monkeypatch.setattr(scan_service, "DriveProvider", lambda _s: _Boom())

    est = await estimate_rebuild(object(), "lib-root")
    assert est.estimated is False
