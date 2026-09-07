"""prompts/15 Stage K — batch priors."""

from sqlalchemy import select

from app.data.models import (
    AIDecision,
    Author,
    Book,
    File,
    FileStatus,
    FileStatusReason,
    Review,
    ReviewStatus,
    Series,
)
from app.services.batch_prior_service import apply_batch_priors


async def _mk(
    session,
    *,
    drive_id: str,
    filename: str,
    author: Author,
    series: Series | None,
    status: FileStatus,
    confidence: int,
) -> File:
    book = Book(canonical_title=filename, author_id=author.id, series_id=series.id if series else None)
    session.add(book)
    await session.flush()
    f = File(
        drive_file_id=drive_id,
        drive_parent_id="p",
        filename=filename,
        sha256=drive_id,
        size_bytes=100,
        status=status,
        status_reason=FileStatusReason.low_confidence if status == FileStatus.review else None,
        book_id=book.id,
    )
    session.add(f)
    await session.flush()
    session.add(
        AIDecision(
            file_id=f.id,
            model="m",
            prompt_hash="p",
            evidence_hash=drive_id,
            raw_response_json={},
            computed_confidence=confidence,
            needs_human_review=confidence < 85,
            reasoning_summary="x",
        )
    )
    if status == FileStatus.review:
        session.add(Review(file_id=f.id, status=ReviewStatus.pending, proposed_json={}))
    await session.flush()
    return f


async def test_batch_consensus_lifts_a_thin_file(db_session) -> None:
    pratchett = Author(name="Terry Pratchett")
    db_session.add(pratchett)
    await db_session.flush()
    discworld = Series(name="Discworld")
    db_session.add(discworld)
    await db_session.flush()

    ids = []
    for i in range(4):
        f = await _mk(
            db_session, drive_id=f"d{i}", filename=f"Pratchett, Terry - Discworld {i} - Book.epub",
            author=pratchett, series=discworld, status=FileStatus.inbox, confidence=95,
        )
        ids.append(f.drive_file_id)
    thin = await _mk(
        db_session, drive_id="thin",
        filename="Pratchett, Terry - Discworld 41 - The Shepherd's Crown.epub",
        author=pratchett, series=discworld, status=FileStatus.review, confidence=74,
    )
    ids.append(thin.drive_file_id)
    await db_session.commit()

    adj = await apply_batch_priors(db_session, ids)

    assert len(adj) == 1 and adj[0].kind == "bumped"
    await db_session.refresh(thin)
    assert thin.status == FileStatus.inbox
    assert thin.status_reason is None
    dec = (
        await db_session.execute(select(AIDecision).where(AIDecision.file_id == thin.id))
    ).scalar_one()
    assert dec.computed_confidence == 86  # 74 + 12
    assert dec.raw_response_json["batch_prior"]["kind"] == "bumped"
    # pending review dropped
    assert (
        await db_session.execute(select(Review).where(Review.file_id == thin.id))
    ).scalar_one_or_none() is None


async def test_no_consensus_no_change(db_session) -> None:
    a1 = Author(name="Author One")
    a2 = Author(name="Author Two")
    db_session.add_all([a1, a2])
    await db_session.flush()

    ids = []
    for i, a in enumerate([a1, a1, a2]):  # only 2 for a1 — below the threshold of 3
        f = await _mk(
            db_session, drive_id=f"n{i}", filename=f"book {i}.epub",
            author=a, series=None, status=FileStatus.inbox, confidence=95,
        )
        ids.append(f.drive_file_id)
    thin = await _mk(
        db_session, drive_id="nt", filename="Author One - A Thin One.epub",
        author=a1, series=None, status=FileStatus.review, confidence=70,
    )
    ids.append(thin.drive_file_id)
    await db_session.commit()

    assert await apply_batch_priors(db_session, ids) == []
    await db_session.refresh(thin)
    assert thin.status == FileStatus.review


async def test_filename_names_consensus_but_id_disagrees_is_a_conflict(db_session) -> None:
    sanderson = Author(name="Brandon Sanderson")
    imposter = Author(name="Someone Else")
    db_session.add_all([sanderson, imposter])
    await db_session.flush()

    ids = []
    for i in range(3):
        f = await _mk(
            db_session, drive_id=f"s{i}", filename=f"Sanderson - {i}.epub",
            author=sanderson, series=None, status=FileStatus.inbox, confidence=96,
        )
        ids.append(f.drive_file_id)
    odd = await _mk(
        db_session, drive_id="odd", filename="Brandon Sanderson - Mystery Book.epub",
        author=imposter, series=None, status=FileStatus.review, confidence=72,
    )
    ids.append(odd.drive_file_id)
    await db_session.commit()

    adj = await apply_batch_priors(db_session, ids)
    assert len(adj) == 1 and adj[0].kind == "conflict"
    await db_session.refresh(odd)
    assert odd.status == FileStatus.review
    dec = (
        await db_session.execute(select(AIDecision).where(AIDecision.file_id == odd.id))
    ).scalar_one()
    assert dec.computed_confidence == 72  # unchanged
    assert dec.raw_response_json["batch_prior"]["kind"] == "conflict"


async def test_idempotent(db_session) -> None:
    pratchett = Author(name="Terry Pratchett")
    db_session.add(pratchett)
    await db_session.flush()
    ids = []
    for i in range(3):
        f = await _mk(
            db_session, drive_id=f"i{i}", filename=f"Pratchett - {i}.epub",
            author=pratchett, series=None, status=FileStatus.inbox, confidence=95,
        )
        ids.append(f.drive_file_id)
    thin = await _mk(
        db_session, drive_id="ii", filename="Terry Pratchett - Thin.epub",
        author=pratchett, series=None, status=FileStatus.review, confidence=80,
    )
    ids.append(thin.drive_file_id)
    await db_session.commit()

    assert len(await apply_batch_priors(db_session, ids)) == 1
    assert await apply_batch_priors(db_session, ids) == []  # second run: no-op
