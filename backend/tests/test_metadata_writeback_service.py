import hashlib
import io
import zipfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.metadata_writeback_service as mw
from app.data.db import Base
from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Operation,
    OperationAction,
    Series,
)
from app.providers.epub.parser import extract_cover
from app.services.metadata_writeback_service import backfill_embedded_metadata
from tests.epub_fixtures import build_epub

_LIMITS = {"max_entry_bytes": 10_000_000, "max_total_bytes": 50_000_000, "max_entries": 1000}


def _jpg() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (300, 450), "purple").save(out, format="JPEG")
    return out.getvalue()


class _FakeProvider:
    def __init__(self, files: dict[str, bytes], *, covers: dict[str, bytes] | None = None) -> None:
        self._files = dict(files)
        self._covers = covers or {}
        self.updates: list[tuple[str, str, bytes]] = []

    def download_file(self, file_id: str) -> bytes:
        if file_id in self._files:
            return self._files[file_id]
        if file_id in self._covers:
            return self._covers[file_id]
        raise KeyError(file_id)

    def update_file_content(self, file_id, *, new_name, data, mime_type="application/epub+zip"):
        self._files[file_id] = data
        self.updates.append((file_id, new_name, data))
        return {"id": file_id, "name": new_name}

    def list_folders(self, parent_id):
        return [{"id": "covers-folder", "name": "covers"}] if self._covers else []

    def list_files_in_folder(self, folder_id):
        return [{"id": cid, "name": f"{cid.removeprefix('cover:')}.jpg"} for cid in self._covers]


@pytest.fixture
async def wb_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wb.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class _CM:
        async def __aenter__(self):
            self._s = factory()
            return self._s

        async def __aexit__(self, *args):
            await self._s.close()
            return False

    monkeypatch.setattr(mw, "async_session_factory", lambda: _CM())
    yield factory
    await engine.dispose()


async def _seed(
    factory,
    *,
    drive_file_id="drive-1",
    filename="Old, Book.epub",
    title="Real Title",
    author="Real Author",
    series=None,
    series_number=None,
    epub_bytes=None,
    embedded_metadata_key=None,
) -> int:
    epub_bytes = epub_bytes if epub_bytes is not None else build_epub()
    async with factory() as s:
        author_row = Author(name=author)
        s.add(author_row)
        await s.flush()
        series_row = None
        if series:
            series_row = Series(name=series)
            s.add(series_row)
            await s.flush()
        book = Book(
            canonical_title=title,
            author_id=author_row.id,
            series_id=series_row.id if series_row else None,
            series_number=series_number,
        )
        s.add(book)
        await s.flush()
        f = File(
            drive_file_id=drive_file_id,
            drive_parent_id="lib",
            filename=filename,
            sha256=hashlib.sha256(epub_bytes).hexdigest(),
            size_bytes=len(epub_bytes),
            status=FileStatus.organised,
            book_id=book.id,
            embedded_metadata_key=embedded_metadata_key,
        )
        s.add(f)
        await s.commit()
        return f.id


async def test_dry_run_touches_nothing_but_logs_operations(wb_db) -> None:
    fid = await _seed(wb_db, drive_file_id="d1")
    provider = _FakeProvider({"d1": build_epub()})

    counts = await backfill_embedded_metadata(
        None, None, dry_run=True, provider_factory=lambda: provider
    )

    assert counts["updated"] == 1
    assert provider.updates == []
    async with wb_db() as s:
        f = await s.get(File, fid)
        assert f.original_sha256 is None
        assert f.embedded_metadata_key is None
        ops = (await s.execute(select(Operation))).scalars().all()
    assert len(ops) == 1
    assert ops[0].action == OperationAction.write_metadata
    assert ops[0].dry_run is True


async def test_live_run_rewrites_file_and_updates_hash(wb_db) -> None:
    original = build_epub(title="messy original", authors=("junk",))
    fid = await _seed(
        wb_db,
        drive_file_id="d1",
        title="Clean Title",
        author="Clean Author",
        series="Clean Series",
        series_number=2.0,
        epub_bytes=original,
    )
    old_sha = hashlib.sha256(original).hexdigest()
    provider = _FakeProvider({"d1": original})

    counts = await backfill_embedded_metadata(
        None, None, dry_run=False, provider_factory=lambda: provider
    )

    assert counts["updated"] == 1
    assert len(provider.updates) == 1
    _fid, new_name, new_bytes = provider.updates[0]
    assert new_name == "Old, Book.epub"  # rename is organize's job, not ours

    async with wb_db() as s:
        f = await s.get(File, fid)
        assert f.original_sha256 == old_sha
        assert f.sha256 == hashlib.sha256(new_bytes).hexdigest()
        assert f.sha256 != old_sha
        assert f.size_bytes == len(new_bytes)
        assert f.embedded_metadata_key == "Clean Title\x1fClean Author\x1fClean Series\x1f2"
        ops = (await s.execute(select(Operation))).scalars().all()
    assert [o.action for o in ops] == [OperationAction.write_metadata]
    assert ops[0].dry_run is False

    from app.providers.epub.parser import parse_epub

    ev = parse_epub(new_bytes, **_LIMITS)
    assert (ev.title, ev.authors, ev.series, ev.series_number) == (
        "Clean Title",
        ["Clean Author"],
        "Clean Series",
        2.0,
    )


async def test_resumable_skips_already_stamped_files(wb_db) -> None:
    key = "Real Title\x1fReal Author\x1f\x1f"
    await _seed(wb_db, drive_file_id="d1", embedded_metadata_key=key)
    provider = _FakeProvider({"d1": build_epub()})

    counts = await backfill_embedded_metadata(
        None, None, dry_run=False, provider_factory=lambda: provider
    )

    assert counts == {"updated": 0, "skipped": 1, "failed": 0, "remaining": 0}
    assert provider.updates == []


async def test_rerun_after_live_is_a_full_skip(wb_db) -> None:
    await _seed(wb_db, drive_file_id="d1", epub_bytes=build_epub(title="x", authors=("y",)))
    provider = _FakeProvider({"d1": build_epub(title="x", authors=("y",))})

    first = await backfill_embedded_metadata(
        None, None, dry_run=False, provider_factory=lambda: provider
    )
    assert first["updated"] == 1
    second = await backfill_embedded_metadata(
        None, None, dry_run=False, provider_factory=lambda: provider
    )
    assert second["updated"] == 0
    assert second["skipped"] == 1
    assert len(provider.updates) == 1  # no second rewrite


async def test_non_epub_files_are_ignored(wb_db) -> None:
    await _seed(wb_db, drive_file_id="d1", filename="Batman 001.cbz")
    provider = _FakeProvider({"d1": b"not even an epub"})

    counts = await backfill_embedded_metadata(
        None, None, dry_run=False, provider_factory=lambda: provider
    )

    assert counts == {"updated": 0, "skipped": 0, "failed": 0, "remaining": 0}
    assert provider.updates == []


async def test_cover_injected_from_covers_folder_when_epub_has_none(wb_db) -> None:
    # An epub with no cover image at all.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
            ' media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
            ' unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>x</dc:title><dc:language>en</dc:language>"
            '<dc:identifier id="id">u</dc:identifier></metadata>'
            '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr("OEBPS/c1.xhtml", "<html><body/></html>")
    coverless = buf.getvalue()
    assert extract_cover(coverless, **_LIMITS) is None

    await _seed(wb_db, drive_file_id="d1", epub_bytes=coverless)
    provider = _FakeProvider({"d1": coverless}, covers={"cover:d1": _jpg()})

    counts = await backfill_embedded_metadata(
        None, "lib-folder", dry_run=False, provider_factory=lambda: provider
    )

    assert counts["updated"] == 1
    _fid, _name, new_bytes = provider.updates[0]
    assert extract_cover(new_bytes, **_LIMITS) is not None
