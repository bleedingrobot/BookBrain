from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.data.models import (
    AIDecision,
    Author,
    Book,
    BookCandidate,
    File,
    FileStatus,
    Identifier,
    IdentifierType,
    Operation,
    OperationAction,
    OperationStatus,
    Review,
    ReviewStatus,
    Setting,
)
from app.core.settings_keys import ORGANIZE_HOLD_HOURS
from app.schemas.reviews import CorrectReviewRequest
from app.services import file_service, recently_organized_service
from app.services.recently_organized_service import parse_since, recently_organized


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 48),
        ("", 48),
        ("24h", 24),
        ("48h", 48),
        ("7d", 168),
        ("12", 12),
        ("0h", 48),  # nonsense -> default
        ("garbage", 48),
        ("999d", 30 * 24),  # clamped to 30 days
    ],
)
def test_parse_since(value, expected) -> None:
    assert parse_since(value) == expected


async def _seed_organized_file(
    db_session,
    *,
    drive_id: str,
    title: str,
    author: str = "Brandon Sanderson",
    organized_hours_ago: float = 1.0,
    confidence: int = 90,
    with_isbn: bool = True,
    provider_candidates: int = 2,
    reasoning: str = "Title and author agree across the EPUB and both providers.",
    model: str = "claude-opus-5",
    status: FileStatus = FileStatus.organised,
    dry_run: bool = False,
    action: OperationAction = OperationAction.move_and_rename,
) -> File:
    author_row = Author(name=author)
    db_session.add(author_row)
    await db_session.flush()
    book = Book(canonical_title=title, author_id=author_row.id)
    db_session.add(book)
    await db_session.flush()
    if with_isbn:
        db_session.add(
            Identifier(book_id=book.id, type=IdentifierType.isbn13, value="9780000000001")
        )

    file_row = File(
        drive_file_id=drive_id,
        drive_parent_id="p",
        filename=f"{title}.epub",
        sha256=f"sha-{drive_id}",
        size_bytes=100,
        status=status,
        book_id=book.id,
    )
    db_session.add(file_row)
    await db_session.flush()

    for i in range(provider_candidates):
        db_session.add(
            BookCandidate(
                file_id=file_row.id,
                title=title,
                author=author,
                source=["google_books", "open_library"][i % 2],
            )
        )
    db_session.add(
        AIDecision(
            file_id=file_row.id,
            model=model,
            prompt_hash="ph",
            evidence_hash="eh",
            raw_response_json={},
            computed_confidence=confidence,
            reasoning_summary=reasoning,
        )
    )
    db_session.add(
        Operation(
            file_id=file_row.id,
            action=action,
            original_name="orig.epub",
            new_name=f"{title}.epub",
            confidence=confidence,
            model=model,
            status=OperationStatus.done,
            dry_run=dry_run,
            timestamp=datetime.now(UTC).replace(tzinfo=None)
            - timedelta(hours=organized_hours_ago),
        )
    )
    await db_session.commit()
    return file_row


async def test_lists_files_organized_in_the_window_newest_first(db_session) -> None:
    await _seed_organized_file(db_session, drive_id="a", title="Elantris", organized_hours_ago=1)
    await _seed_organized_file(
        db_session, drive_id="b", title="Warbreaker", organized_hours_ago=10
    )
    # Outside a 48h window.
    await _seed_organized_file(
        db_session, drive_id="c", title="Mistborn", organized_hours_ago=100
    )

    result = await recently_organized(db_session, since_hours=48)

    assert [i.title for i in result.organized] == ["Elantris", "Warbreaker"]
    assert result.since_hours == 48
    assert result.hold_hours == 0
    assert result.held == []


async def test_excludes_dry_run_operations(db_session) -> None:
    await _seed_organized_file(
        db_session, drive_id="d", title="Dry Run Book", dry_run=True, organized_hours_ago=1
    )
    result = await recently_organized(db_session, since_hours=48)
    assert result.organized == []


async def test_evidence_summary_shape(db_session) -> None:
    await _seed_organized_file(
        db_session,
        drive_id="e",
        title="The Way of Kings",
        confidence=97,
        reasoning="Strong agreement across every source.",
    )
    result = await recently_organized(db_session, since_hours=48)
    item = result.organized[0]
    assert item.confidence == 97
    assert item.current_status == "organised"
    assert item.confirmed is False
    assert "Strong agreement across every source." in item.evidence_summary
    assert "ISBN in file" in item.evidence_summary
    assert "2 provider matches" in item.evidence_summary


async def test_deterministic_model_evidence_summary(db_session) -> None:
    await _seed_organized_file(
        db_session,
        drive_id="det",
        title="Neuromancer",
        model="deterministic",
        reasoning="whatever",
    )
    result = await recently_organized(db_session, since_hours=48)
    assert "Deterministic" in result.organized[0].evidence_summary


async def test_one_row_per_file_uses_latest_operation(db_session) -> None:
    file_row = await _seed_organized_file(
        db_session, drive_id="f", title="Skyward", organized_hours_ago=5
    )
    db_session.add(
        Operation(
            file_id=file_row.id,
            action=OperationAction.move_and_rename,
            new_name="Skyward.epub",
            confidence=88,
            status=OperationStatus.done,
            dry_run=False,
            timestamp=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    result = await recently_organized(db_session, since_hours=48)
    assert len(result.organized) == 1
    # The newer op (1h ago) wins.
    assert result.organized[0].organized_at > (
        datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    ).isoformat()


async def test_confirmed_flag_and_confirm_file(db_session) -> None:
    file_row = await _seed_organized_file(db_session, drive_id="g", title="Tress")

    await file_service.confirm_file(db_session, file_row.id)
    # idempotent
    await file_service.confirm_file(db_session, file_row.id)

    reviews = (await db_session.execute(select(Review))).scalars().all()
    assert len(reviews) == 1
    assert reviews[0].status == ReviewStatus.approved
    assert reviews[0].proposed_json["confirmed"] is True

    result = await recently_organized(db_session, since_hours=48)
    assert result.organized[0].confirmed is True
    # confirm must not move the file
    await db_session.refresh(file_row)
    assert file_row.status == FileStatus.organised


async def test_confirm_rejects_unidentified_file(db_session) -> None:
    file_row = File(
        drive_file_id="u",
        drive_parent_id="p",
        filename="u.epub",
        sha256="sha-u",
        size_bytes=1,
        status=FileStatus.unidentified,
    )
    db_session.add(file_row)
    await db_session.commit()
    with pytest.raises(file_service.FileNotIdentifiedError):
        await file_service.confirm_file(db_session, file_row.id)


async def test_correct_from_tray_shows_current_status(db_session) -> None:
    file_row = await _seed_organized_file(db_session, drive_id="h", title="Wrong Title")
    await file_service.correct_file(
        db_session,
        file_row.id,
        CorrectReviewRequest(title="Right Title", author="Brandon Sanderson"),
    )
    result = await recently_organized(db_session, since_hours=48)
    item = result.organized[0]
    # The correction pulled it back to inbox — the tray reflects that.
    assert item.current_status == "inbox"


async def test_held_files_listed_when_hold_on(db_session) -> None:
    db_session.add(Setting(key=ORGANIZE_HOLD_HOURS, value="24"))
    # A fresh auto-eligible file, sitting in inbox.
    await _seed_organized_file(
        db_session,
        drive_id="held1",
        title="Held Book",
        status=FileStatus.inbox,
        action=OperationAction.move_and_rename,
        dry_run=True,  # keep it out of the organized list
        organized_hours_ago=0.1,
    )
    await db_session.commit()

    result = await recently_organized(db_session, since_hours=48)
    assert result.hold_hours == 24
    assert len(result.held) == 1
    held = result.held[0]
    assert held.title == "Held Book"
    assert held.eligible_at > held.held_since
