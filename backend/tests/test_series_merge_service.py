from sqlalchemy import select

from app.data.models import (
    Author,
    Book,
    File,
    FileStatus,
    Operation,
    OperationAction,
    OperationStatus,
    Series,
)
from app.providers.ai.types import AISeriesMergeResult
from app.services.series_merge_service import (
    SeriesMergeValidationError,
    apply_series_merge,
    propose_series_merge,
)


class _FakeAiClient:
    def __init__(self, result: AISeriesMergeResult) -> None:
        self._result = result
        self.prompts: list[str] = []

    async def propose_series_merge(self, prompt: str) -> AISeriesMergeResult:
        self.prompts.append(prompt)
        return self._result


class _FakeDriveProvider:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.folders: dict[str, list[dict]] = {}
        self.move_calls: list[dict] = []
        self._next_id = 0
        self._fail_for = fail_for or set()

    def list_folders(self, parent_id: str | None) -> list[dict]:
        return self.folders.get(parent_id or "root", [])

    def create_folder(self, name: str, parent_id: str | None = None) -> dict:
        self._next_id += 1
        folder = {"id": f"folder-{self._next_id}", "name": name}
        self.folders.setdefault(parent_id or "root", []).append(folder)
        return folder

    def move_and_rename(self, file_id, *, old_parent_id, new_parent_id, new_name) -> dict:
        if file_id in self._fail_for:
            raise RuntimeError("simulated Drive failure")
        self.move_calls.append(
            {
                "file_id": file_id,
                "old_parent_id": old_parent_id,
                "new_parent_id": new_parent_id,
                "new_name": new_name,
            }
        )
        return {"id": file_id, "name": new_name, "parents": [new_parent_id]}


async def _seed_series(db_session, *, name: str, author_name: str, books: list[dict]) -> Series:
    author = Author(name=author_name)
    db_session.add(author)
    await db_session.flush()

    series = Series(name=name)
    db_session.add(series)
    await db_session.flush()

    for spec in books:
        book = Book(
            canonical_title=spec["title"],
            author_id=author.id,
            series_id=series.id,
            series_number=spec.get("series_number"),
        )
        db_session.add(book)
        await db_session.flush()
        for i, drive_id in enumerate(spec.get("files", [])):
            db_session.add(
                File(
                    drive_file_id=drive_id,
                    drive_parent_id="old-parent",
                    filename=f"{spec['title']}-{i}.epub",
                    sha256=f"hash-{drive_id}",
                    size_bytes=100,
                    status=FileStatus.organised,
                    book_id=book.id,
                )
            )
    await db_session.commit()
    return series


async def test_propose_series_merge_accepts_valid_canonical_name(db_session) -> None:
    series_a = await _seed_series(
        db_session, name="Empire of the Vampire", author_name="Jay Kristoff", books=[{"title": "Book 1"}]
    )
    series_b = await _seed_series(
        db_session, name="Vampire Earth", author_name="Jay Kristoff", books=[{"title": "Book 2"}]
    )
    ai_client = _FakeAiClient(
        AISeriesMergeResult(
            is_same_series=True,
            canonical_series_name="Vampire Earth",
            excluded_series_names=[],
            confidence=90,
            explanation="Same saga, split across two identification passes.",
            warnings=[],
        )
    )

    proposal = await propose_series_merge(db_session, [series_a.id, series_b.id], ai_client)

    assert proposal.is_same_series is True
    assert proposal.canonical_series_name == "Vampire Earth"
    assert proposal.excluded_series_names == []
    assert proposal.confidence == 90
    assert {s.name for s in proposal.series} == {"Empire of the Vampire", "Vampire Earth"}
    assert proposal.plan.series_to_delete == ["Empire of the Vampire"]


async def test_propose_series_merge_plan_shows_exact_moves_and_excludes(db_session) -> None:
    unrelated = await _seed_series(
        db_session, name="Book of Night", author_name="Holly Black", books=[{"title": "Book of Night"}]
    )
    variant = await _seed_series(
        db_session,
        name="The Night Angel Trilogy",
        author_name="Brent Weeks",
        books=[
            {"title": "Shadow's Edge", "series_number": 2.0, "files": ["f-shadow"]},
            {"title": "Beyond the Shadows", "series_number": 3.0, "files": ["f-beyond"]},
        ],
    )
    canonical = await _seed_series(
        db_session,
        name="Night Angel Trilogy",
        author_name="Brent Weeks",
        books=[{"title": "The Way of Shadows", "series_number": 1.0, "files": ["f-way"]}],
    )
    ai_client = _FakeAiClient(
        AISeriesMergeResult(
            is_same_series=True,
            canonical_series_name="Night Angel Trilogy",
            excluded_series_names=["Book of Night"],
            confidence=93,
            explanation="Same Brent Weeks trilogy; Book of Night is unrelated.",
            warnings=[],
        )
    )

    proposal = await propose_series_merge(
        db_session, [unrelated.id, variant.id, canonical.id], ai_client
    )

    plan = proposal.plan
    assert plan.series_to_delete == ["The Night Angel Trilogy"]
    assert {m.current_filename for m in plan.moves} == {"Shadow's Edge-0.epub", "Beyond the Shadows-0.epub"}
    shadow_move = next(m for m in plan.moves if m.book_title == "Shadow's Edge")
    assert shadow_move.from_series_name == "The Night Angel Trilogy"
    assert shadow_move.new_folder_path == "Brent Weeks/Night Angel Trilogy"
    assert shadow_move.new_filename == "Brent Weeks, Shadow's Edge, Night Angel Trilogy, 2.epub"
    # "Book of Night" is excluded — nothing about it appears in the plan at all.
    assert all(m.book_title != "Book of Night" for m in plan.moves)
    assert "Book of Night" not in plan.series_to_delete


async def test_propose_series_merge_rejects_hallucinated_name(db_session) -> None:
    series_a = await _seed_series(db_session, name="Series A", author_name="Author", books=[{"title": "Book 1"}])
    series_b = await _seed_series(db_session, name="Series B", author_name="Author", books=[{"title": "Book 2"}])
    ai_client = _FakeAiClient(
        AISeriesMergeResult(
            is_same_series=True,
            canonical_series_name="A Brand New Name Nobody Gave It",
            excluded_series_names=[],
            confidence=95,
            explanation="...",
            warnings=[],
        )
    )

    proposal = await propose_series_merge(db_session, [series_a.id, series_b.id], ai_client)

    assert proposal.is_same_series is False
    assert proposal.confidence == 0
    assert any("unrecognised name" in w for w in proposal.warnings)


async def test_propose_series_merge_requires_at_least_two_series(db_session) -> None:
    series_a = await _seed_series(db_session, name="Solo Series", author_name="Author", books=[{"title": "Book"}])

    try:
        await propose_series_merge(db_session, [series_a.id], _FakeAiClient(None))
        assert False, "expected SeriesMergeValidationError"
    except SeriesMergeValidationError:
        pass


async def test_apply_series_merge_moves_files_and_repoints_books(db_session) -> None:
    series_a = await _seed_series(
        db_session,
        name="Empire of the Vampire",
        author_name="Jay Kristoff",
        books=[{"title": "Book One", "series_number": 1.0, "files": ["drive-1"]}],
    )
    series_b = await _seed_series(
        db_session,
        name="Vampire Earth",
        author_name="Jay Kristoff",
        books=[{"title": "Book Two", "series_number": 2.0, "files": ["drive-2"]}],
    )
    provider = _FakeDriveProvider()

    result = await apply_series_merge(
        db_session,
        provider,
        "root-id",
        series_ids=[series_a.id, series_b.id],
        canonical_series_name="Vampire Earth",
        confirm_same_series=True,
    )

    assert result.canonical_series_name == "Vampire Earth"
    assert result.moved_files == 1
    assert result.repointed_books == 1
    assert result.failed_files == []
    assert result.deleted_series_ids == [series_a.id]  # emptied out, so removed

    moved_file = (
        await db_session.execute(select(File).where(File.drive_file_id == "drive-1"))
    ).scalar_one()
    assert "Vampire Earth" in moved_file.filename

    # Regression: the repointed book must actually end up pointing at the
    # canonical series once the old (now-empty) series row is deleted in the
    # same commit — a raw `book.series_id = ...` assignment silently got
    # reverted to None by SQLAlchemy's default parent-delete FK-null
    # behavior, since the old series' `.books` collection (loaded via
    # selectinload) still listed this book as a member.
    moved_book = (
        await db_session.execute(select(Book).where(Book.id == moved_file.book_id))
    ).scalar_one()
    assert moved_book.series_id == series_b.id

    remaining_series = (await db_session.execute(select(Series))).scalars().all()
    assert [s.id for s in remaining_series] == [series_b.id]

    ops = (await db_session.execute(select(Operation))).scalars().all()
    assert len(ops) == 1
    assert ops[0].status == OperationStatus.done
    assert ops[0].file_id == moved_file.id
    # Logged as series_merge, not move_and_rename — so undo_operation refuses
    # it (undoing would land the file in the deleted source folder).
    assert ops[0].action == OperationAction.series_merge

    # prompts/15 Stage J — the merged-away name is now an alias of the
    # canonical series, so a fresh scan phrasing it the old way won't re-fork.
    from app.data.models import SeriesAlias

    aliases = (await db_session.execute(select(SeriesAlias))).scalars().all()
    assert [(a.series_id, a.alias) for a in aliases] == [
        (series_b.id, "Empire of the Vampire")
    ]


async def test_apply_series_merge_leaves_excluded_series_untouched(db_session) -> None:
    # Regression for a real near-miss: a 3-member cluster where two entries
    # are genuinely the same series and one is an unrelated false positive
    # that only shares a word. Applying the fix must not fold the excluded
    # one in just because it was part of the same similarity-flagged cluster.
    unrelated = await _seed_series(
        db_session,
        name="Book of Night",
        author_name="Holly Black",
        books=[{"title": "Book of Night", "files": ["holly-black-file"]}],
    )
    variant = await _seed_series(
        db_session,
        name="The Night Angel Trilogy",
        author_name="Brent Weeks",
        books=[{"title": "Shadow's Edge", "series_number": 2.0, "files": ["weeks-file"]}],
    )
    canonical = await _seed_series(
        db_session,
        name="Night Angel Trilogy",
        author_name="Brent Weeks",
        books=[{"title": "The Way of Shadows", "series_number": 1.0, "files": ["weeks-file-1"]}],
    )
    provider = _FakeDriveProvider()

    result = await apply_series_merge(
        db_session,
        provider,
        "root-id",
        series_ids=[unrelated.id, variant.id, canonical.id],
        canonical_series_name="Night Angel Trilogy",
        excluded_series_names=["Book of Night"],
        confirm_same_series=True,
    )

    assert result.moved_files == 1  # only the "weeks-file" from `variant`
    assert result.repointed_books == 1
    assert result.deleted_series_ids == [variant.id]  # only the real match's series was removed

    # The unrelated series and its book/file are completely untouched.
    assert all(call["file_id"] == "weeks-file" for call in provider.move_calls)
    untouched_series = (
        await db_session.execute(select(Series).where(Series.id == unrelated.id))
    ).scalar_one_or_none()
    assert untouched_series is not None
    untouched_book = (
        await db_session.execute(select(Book).where(Book.canonical_title == "Book of Night"))
    ).scalar_one()
    assert untouched_book.series_id == unrelated.id
    untouched_file = (
        await db_session.execute(select(File).where(File.drive_file_id == "holly-black-file"))
    ).scalar_one()
    assert untouched_file.drive_parent_id == "old-parent"  # never moved


async def test_apply_series_merge_rejects_unknown_canonical_name(db_session) -> None:
    series_a = await _seed_series(db_session, name="Series A", author_name="Author", books=[{"title": "Book 1"}])
    series_b = await _seed_series(db_session, name="Series B", author_name="Author", books=[{"title": "Book 2"}])
    provider = _FakeDriveProvider()

    try:
        await apply_series_merge(
            db_session,
            provider,
            "root-id",
            series_ids=[series_a.id, series_b.id],
            canonical_series_name="Not A Real Name",
            confirm_same_series=True,
        )
        assert False, "expected SeriesMergeValidationError"
    except SeriesMergeValidationError:
        pass


async def test_apply_series_merge_rejects_without_confirm_same_series(db_session) -> None:
    # Regression: a frontend bug once let "Apply fix" render even when
    # Claude had said these are two genuinely different series. This flag
    # makes that acknowledgement mandatory at the API layer too, not just a
    # UI-only gate.
    series_a = await _seed_series(db_session, name="Series A", author_name="Author", books=[{"title": "Book 1"}])
    series_b = await _seed_series(db_session, name="Series B", author_name="Author", books=[{"title": "Book 2"}])
    provider = _FakeDriveProvider()

    try:
        await apply_series_merge(
            db_session,
            provider,
            "root-id",
            series_ids=[series_a.id, series_b.id],
            canonical_series_name="Series B",
            confirm_same_series=False,
        )
        assert False, "expected SeriesMergeValidationError"
    except SeriesMergeValidationError:
        pass

    assert provider.move_calls == []


async def test_apply_series_merge_leaves_book_unmerged_on_partial_failure(db_session) -> None:
    series_a = await _seed_series(
        db_session,
        name="Series A",
        author_name="Author",
        books=[{"title": "Multi-file Book", "series_number": 1.0, "files": ["ok-file", "bad-file"]}],
    )
    series_b = await _seed_series(db_session, name="Series B", author_name="Author", books=[{"title": "Other"}])
    provider = _FakeDriveProvider(fail_for={"bad-file"})

    result = await apply_series_merge(
        db_session,
        provider,
        "root-id",
        series_ids=[series_a.id, series_b.id],
        canonical_series_name="Series B",
        confirm_same_series=True,
    )

    assert result.moved_files == 1
    assert len(result.failed_files) == 1
    assert result.repointed_books == 0
    assert len(result.skipped_books) == 1
    assert result.deleted_series_ids == []  # series A still has its unmerged book

    book = (
        await db_session.execute(select(Book).where(Book.canonical_title == "Multi-file Book"))
    ).scalar_one()
    assert book.series_id == series_a.id  # left on its original series


async def test_apply_series_merge_is_idempotent_on_retry(db_session) -> None:
    # A book with two files: one will move successfully, the other will
    # fail — so the book isn't repointed and series A survives to be
    # retried. On retry (failure now resolved), the already-moved file must
    # be recognised as already-in-place rather than re-moved or re-logged.
    series_a = await _seed_series(
        db_session,
        name="Series A",
        author_name="Author",
        books=[{"title": "Book", "series_number": 1.0, "files": ["good-file", "bad-file"]}],
    )
    series_b = await _seed_series(db_session, name="Series B", author_name="Author", books=[{"title": "Other"}])
    provider = _FakeDriveProvider(fail_for={"bad-file"})

    first = await apply_series_merge(
        db_session,
        provider,
        "root-id",
        series_ids=[series_a.id, series_b.id],
        canonical_series_name="Series B",
        confirm_same_series=True,
    )
    assert first.moved_files == 1
    assert len(first.failed_files) == 1
    assert first.repointed_books == 0  # not all files succeeded yet

    provider._fail_for.clear()  # the transient failure is "fixed" now
    second = await apply_series_merge(
        db_session,
        provider,
        "root-id",
        series_ids=[series_a.id, series_b.id],
        canonical_series_name="Series B",
        confirm_same_series=True,
    )
    assert second.moved_files == 1  # only bad-file, this time
    assert second.already_in_place_files == 1  # good-file recognised, not re-moved
    assert second.failed_files == []
    assert second.repointed_books == 1
    assert second.deleted_series_ids == [series_a.id]

    ops = (await db_session.execute(select(Operation))).scalars().all()
    assert len(ops) == 2  # one per file, never duplicated for good-file
