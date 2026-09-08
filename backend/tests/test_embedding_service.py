import hashlib
import json

import numpy as np
from sqlalchemy import select

from app.data.models import Author, Book, File, FileStatus, MetadataSource, Series
from app.services.embedding_service import DIM, MODEL_ID, embed_input, refresh_embeddings
from app.services.library_index_service import build_embeddings_payload


def _fake_embed(texts: list[str]) -> np.ndarray:
    """Deterministic unit vectors seeded from the text — no model download."""
    out = np.empty((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        seed = int.from_bytes(hashlib.sha1(t.encode()).digest()[:4], "little")
        v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
        out[i] = v / np.linalg.norm(v)
    return out


async def _seed_book(db_session, title, author=None, series=None, desc=None, drive_id=None) -> Book:
    a = Author(name=author) if author else None
    s = Series(name=series) if series else None
    for x in (a, s):
        if x is not None:
            db_session.add(x)
    await db_session.flush()
    book = Book(
        canonical_title=title,
        author_id=a.id if a else None,
        series_id=s.id if s else None,
        description=desc,
    )
    db_session.add(book)
    await db_session.flush()
    db_session.add(
        File(
            drive_file_id=drive_id or f"d-{title}",
            filename=f"{title}.epub",
            sha256=title,
            size_bytes=1,
            status=FileStatus.organised,
            book_id=book.id,
        )
    )
    await db_session.commit()
    return book


def test_embed_input_assembles_and_drops_missing_parts() -> None:
    assert embed_input("Dune", "Frank Herbert", "Dune", "Desert planet.") == (
        "Dune. Frank Herbert. Dune. Desert planet."
    )
    assert embed_input("Solo Book", None, None, None) == "Solo Book"
    assert embed_input("T", "A", None, "  ") == "T. A"  # whitespace blurb dropped


async def test_refresh_is_incremental(db_session) -> None:
    await _seed_book(db_session, "A", "Auth A", desc="about a lighthouse")
    await _seed_book(db_session, "B", "Auth B", desc="about a spaceship")

    r1 = await refresh_embeddings(db_session, embed_fn=_fake_embed)
    assert r1 == {"embedded": 2, "unchanged": 0, "pending": 0}

    books = (await db_session.execute(select(Book))).scalars().all()
    for b in books:
        assert b.embedding is not None and len(b.embedding) == DIM * 4
        assert b.embedding_hash and b.embedding_model == MODEL_ID

    # nothing changed → no work
    r2 = await refresh_embeddings(db_session, embed_fn=_fake_embed)
    assert r2 == {"embedded": 0, "unchanged": 2, "pending": 0}


async def test_a_changed_blurb_is_re_embedded(db_session) -> None:
    book = await _seed_book(db_session, "A", "Auth", desc="first blurb")
    await refresh_embeddings(db_session, embed_fn=_fake_embed)
    before = book.embedding

    book.description = "a totally different blurb about pirates"
    await db_session.commit()

    r = await refresh_embeddings(db_session, embed_fn=_fake_embed)
    assert r["embedded"] == 1 and r["unchanged"] == 0
    await db_session.refresh(book)
    assert book.embedding != before


async def test_limit_caps_the_run(db_session) -> None:
    for i in range(5):
        await _seed_book(db_session, f"B{i}", f"Auth {i}", desc=f"blurb {i}")
    r = await refresh_embeddings(db_session, limit=2, embed_fn=_fake_embed)
    assert r == {"embedded": 2, "unchanged": 0, "pending": 3}


async def test_epub_description_is_used_when_book_has_none(db_session) -> None:
    book = await _seed_book(db_session, "A", "Auth", desc=None, drive_id="d-A")
    file_id = (
        await db_session.execute(select(File.id).where(File.drive_file_id == "d-A"))
    ).scalar_one()
    db_session.add(
        MetadataSource(
            file_id=file_id, field_name="description", value="epub blurb about robots", source="epub"
        )
    )
    await db_session.commit()

    captured: list[str] = []

    def _spy(texts):
        captured.extend(texts)
        return _fake_embed(texts)

    await refresh_embeddings(db_session, embed_fn=_spy)
    assert "epub blurb about robots" in captured[0]


async def test_build_embeddings_payload_roundtrips(db_session) -> None:
    await _seed_book(db_session, "A", "Auth A", desc="x", drive_id="drive-a")
    await _seed_book(db_session, "B", "Auth B", desc="y", drive_id="drive-b")
    await _seed_book(db_session, "C", "Auth C", desc="z")  # left un-embedded
    await refresh_embeddings(db_session, limit=2, embed_fn=_fake_embed)

    data = await build_embeddings_payload(db_session)
    hlen = int.from_bytes(data[:4], "little")
    header = json.loads(data[4 : 4 + hlen])

    assert header["version"] == 1
    assert header["model"] == MODEL_ID
    assert header["dim"] == DIM
    assert header["count"] == 2
    assert set(header["ids"]) == {"drive-a", "drive-b"}  # C skipped

    block = np.frombuffer(data[4 + hlen :], dtype=np.int8).reshape(2, DIM)
    # int8 dequant is within ~1/127 of the stored float32
    a_book = (
        await db_session.execute(select(Book).where(Book.canonical_title == "A"))
    ).scalar_one()
    stored = np.frombuffer(a_book.embedding, dtype=np.float32)
    idx = header["ids"].index("drive-a")
    assert np.allclose(block[idx].astype(np.float32) / 127.0, stored, atol=0.02)


async def test_payload_is_a_valid_header_when_empty(db_session) -> None:
    data = await build_embeddings_payload(db_session)
    hlen = int.from_bytes(data[:4], "little")
    header = json.loads(data[4 : 4 + hlen])
    assert header["count"] == 0 and header["ids"] == []
    assert len(data) == 4 + hlen
