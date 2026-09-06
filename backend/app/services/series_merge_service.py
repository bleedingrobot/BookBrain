import asyncio
import logging
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.models import Book, Operation, OperationAction, OperationStatus, Series, SeriesAlias
from app.providers.ai.anthropic_client import AnthropicIdentificationClient
from app.providers.drive.provider import DriveProvider
from app.schemas.series_merge import (
    SeriesMergeBookInfo,
    SeriesMergeBookSkip,
    SeriesMergeFileFailure,
    SeriesMergePlan,
    SeriesMergePlannedMove,
    SeriesMergeProposal,
    SeriesMergeResult,
    SeriesMergeSeriesInfo,
)
from app.services.organize_service import build_target_path, get_folder_path_cache

logger = logging.getLogger(__name__)

# Serializes this service's own SQLite commits, mirroring
# OrganizeService._write_lock's reasoning — a merge apply running alongside
# a concurrent organize/scan shouldn't hit a Windows fsync-driven busy-timeout.
# Module-level (not per-instance, there being no service class here), so
# tests must reset it the same way conftest.py already resets the folder
# path cache and book write lock: an asyncio.Lock binds to the event loop of
# its first real acquisition, and pytest-asyncio gives each test its own loop.
_write_lock = asyncio.Lock()


def reset_series_merge_write_lock() -> None:
    global _write_lock
    _write_lock = asyncio.Lock()


class SeriesMergeValidationError(Exception):
    """Raised when the requested series_ids don't resolve to a real cluster,
    or canonical_series_name doesn't match one of the cluster's current
    series names."""


async def _load_series_cluster(session: AsyncSession, series_ids: list[int]) -> list[Series]:
    rows = (
        (
            await session.execute(
                select(Series)
                .where(Series.id.in_(series_ids))
                .options(
                    selectinload(Series.books).selectinload(Book.files),
                    selectinload(Series.books).selectinload(Book.author),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != len(set(series_ids)):
        raise SeriesMergeValidationError("one or more series in this cluster no longer exist")
    return list(rows)


def _sorted_books(series: Series) -> list[Book]:
    return sorted(series.books, key=lambda b: (b.series_number is None, b.series_number or 0))


def _to_series_info(series: Series) -> SeriesMergeSeriesInfo:
    return SeriesMergeSeriesInfo(
        id=series.id,
        name=series.name,
        books=[
            SeriesMergeBookInfo(
                id=b.id,
                canonical_title=b.canonical_title,
                series_number=b.series_number,
                author_name=b.author.name if b.author else None,
                file_count=len(b.files),
            )
            for b in _sorted_books(series)
        ],
    )


def _build_plan(
    series_rows: list[Series], canonical_series_name: str, excluded_series_names: set[str]
) -> SeriesMergePlan:
    """Exactly what apply_series_merge will do if every file move succeeds —
    computed with the same pure build_target_path apply itself uses, so the
    preview shown next to "Apply fix" can never drift from the real
    behavior. No Drive calls: the destination folder is shown as a path
    string, not resolved to a live folder id (that only happens at apply
    time, and may create the folder if it doesn't exist yet)."""
    canonical = next((s for s in series_rows if s.name == canonical_series_name), None)
    if canonical is None:
        return SeriesMergePlan(moves=[], series_to_delete=[])

    others = [
        s for s in series_rows if s.id != canonical.id and s.name not in excluded_series_names
    ]
    moves: list[SeriesMergePlannedMove] = []
    for series in others:
        for book in _sorted_books(series):
            for file_row in book.files:
                folders, filename = build_target_path(
                    title=book.canonical_title,
                    author_name=book.author.name if book.author else None,
                    series_name=canonical.name,
                    series_number=book.series_number,
                    extension=PurePosixPath(file_row.filename).suffix.lower() or ".epub",
                )
                moves.append(
                    SeriesMergePlannedMove(
                        book_title=book.canonical_title,
                        from_series_name=series.name,
                        current_filename=file_row.filename,
                        new_filename=filename,
                        new_folder_path="/".join(folders),
                    )
                )
    # Every "other" series ends up with zero books once its books all move
    # (or had none to begin with) — apply deletes any such series, so a
    # full-success preview shows all of them as deleted.
    return SeriesMergePlan(moves=moves, series_to_delete=[s.name for s in others])


def _build_merge_prompt(series_rows: list[Series]) -> str:
    lines = [
        "These Series records in a book library have similar names and might be "
        "the same series split across multiple database rows. Decide whether "
        "they're really one series, and if so, which existing name is canonical.",
    ]
    for series in series_rows:
        lines.append("")
        lines.append(f"Series: {series.name!r} (id={series.id}, {len(series.books)} book(s))")
        for b in _sorted_books(series):
            author = b.author.name if b.author else "(no author)"
            number = b.series_number if b.series_number is not None else "(none)"
            lines.append(
                f"  - {b.canonical_title!r} by {author}, series_number={number}, "
                f"{len(b.files)} file(s)"
            )
    return "\n".join(lines)


async def propose_series_merge(
    session: AsyncSession,
    series_ids: list[int],
    ai_client: AnthropicIdentificationClient | None = None,
) -> SeriesMergeProposal:
    if len(set(series_ids)) < 2:
        raise SeriesMergeValidationError("a merge needs at least two series")
    series_rows = await _load_series_cluster(session, series_ids)
    client = ai_client or AnthropicIdentificationClient()

    prompt = _build_merge_prompt(series_rows)
    ai_result = await client.propose_series_merge(prompt)

    valid_names = {s.name for s in series_rows}
    if ai_result.canonical_series_name not in valid_names:
        # Hallucination guard — never trust an invented name. Degrade to "not
        # confident enough to propose a fix" rather than silently picking one.
        return SeriesMergeProposal(
            is_same_series=False,
            canonical_series_name=series_rows[0].name,
            excluded_series_names=[],
            confidence=0,
            explanation=(
                "The AI's suggested canonical name didn't match any of this cluster's "
                "existing series names, so no automatic fix is proposed — please review manually."
            ),
            warnings=[f"Model proposed an unrecognised name: {ai_result.canonical_series_name!r}"],
            series=[_to_series_info(s) for s in series_rows],
            plan=SeriesMergePlan(moves=[], series_to_delete=[]),
        )

    # Defensive cleanup rather than a hard rejection: drop any excluded name
    # that isn't actually one of this cluster's series (or is the canonical
    # name itself) instead of trusting it outright — this list only ever
    # narrows what apply touches, so a stray/invalid entry is harmless to drop.
    excluded_series_names = [
        name
        for name in ai_result.excluded_series_names
        if name in valid_names and name != ai_result.canonical_series_name
    ]

    return SeriesMergeProposal(
        is_same_series=ai_result.is_same_series,
        canonical_series_name=ai_result.canonical_series_name,
        excluded_series_names=excluded_series_names,
        confidence=int(ai_result.confidence),
        explanation=ai_result.explanation,
        warnings=ai_result.warnings,
        series=[_to_series_info(s) for s in series_rows],
        plan=_build_plan(series_rows, ai_result.canonical_series_name, set(excluded_series_names)),
    )


async def apply_series_merge(
    session: AsyncSession,
    provider: DriveProvider,
    library_root_folder_id: str,
    *,
    series_ids: list[int],
    canonical_series_name: str,
    confirm_same_series: bool,
    excluded_series_names: list[str] | None = None,
) -> SeriesMergeResult:
    if not confirm_same_series:
        raise SeriesMergeValidationError(
            "cannot apply a merge without confirming these are the same series"
        )
    series_rows = await _load_series_cluster(session, series_ids)
    canonical = next((s for s in series_rows if s.name == canonical_series_name), None)
    if canonical is None:
        raise SeriesMergeValidationError(
            "canonical_series_name must exactly match one of this cluster's current series names"
        )
    # A cluster can have more than two members — excluded_series_names are
    # left completely untouched (not moved, not deleted), not folded into
    # canonical just because they were part of the same similarity flag.
    excluded = set(excluded_series_names or [])
    others = [s for s in series_rows if s.id != canonical.id and s.name not in excluded]

    folder_cache = get_folder_path_cache()
    moved_files = 0
    already_in_place = 0
    failed_files: list[SeriesMergeFileFailure] = []
    repointed_books = 0
    skipped_books: list[SeriesMergeBookSkip] = []

    for series in others:
        for book in list(series.books):
            file_outcomes: list[bool] = []
            for file_row in book.files:
                try:
                    folders, filename = build_target_path(
                        title=book.canonical_title,
                        author_name=book.author.name if book.author else None,
                        series_name=canonical.name,
                        series_number=book.series_number,
                        extension=PurePosixPath(file_row.filename).suffix.lower() or ".epub",
                    )
                    target_folder_id = await folder_cache.resolve(
                        provider, library_root_folder_id, folders
                    )

                    if file_row.drive_parent_id == target_folder_id and file_row.filename == filename:
                        # Already correct — e.g. left over from a prior partial apply.
                        already_in_place += 1
                        file_outcomes.append(True)
                        continue

                    await asyncio.to_thread(
                        provider.move_and_rename,
                        file_row.drive_file_id,
                        old_parent_id=file_row.drive_parent_id,
                        new_parent_id=target_folder_id,
                        new_name=filename,
                    )
                    session.add(
                        Operation(
                            file_id=file_row.id,
                            action=OperationAction.series_merge,
                            original_name=file_row.filename,
                            original_parent_id=file_row.drive_parent_id,
                            new_name=filename,
                            new_parent_id=target_folder_id,
                            status=OperationStatus.done,
                            dry_run=False,
                            reason=f"series merge: {series.name!r} -> {canonical.name!r}",
                        )
                    )
                    file_row.filename = filename
                    file_row.drive_parent_id = target_folder_id
                    moved_files += 1
                    file_outcomes.append(True)
                except Exception as exc:
                    logger.exception("series merge: failed to move file %s", file_row.id)
                    failed_files.append(
                        SeriesMergeFileFailure(
                            file_id=file_row.id, filename=file_row.filename, reason=str(exc)
                        )
                    )
                    file_outcomes.append(False)

            if all(file_outcomes):  # vacuously true for a book with zero files
                # Assign through the relationship, not the raw series_id column:
                # `series` was loaded via selectinload, so its `.books` collection
                # still lists `book` in memory. A bare `book.series_id = ...`
                # leaves that stale collection membership untouched — when the
                # now-supposedly-empty `series` is deleted below, SQLAlchemy's
                # default one-to-many behavior nulls the FK of every object
                # still present in that collection, silently reverting this
                # assignment back to None in the same commit. Setting `.series`
                # updates both sides' collections and avoids that entirely.
                book.series = canonical
                repointed_books += 1
            else:
                skipped_books.append(
                    SeriesMergeBookSkip(
                        book_id=book.id,
                        canonical_title=book.canonical_title,
                        reason="one or more files failed to move; left on its original series",
                    )
                )

    await session.flush()

    deleted_series_ids: list[int] = []
    for series in others:
        remaining = (
            await session.execute(
                select(func.count()).select_from(Book).where(Book.series_id == series.id)
            )
        ).scalar_one()
        if remaining == 0:
            await session.execute(delete(SeriesAlias).where(SeriesAlias.series_id == series.id))
            await session.delete(series)
            deleted_series_ids.append(series.id)

    async with _write_lock:
        await session.commit()

    return SeriesMergeResult(
        canonical_series_id=canonical.id,
        canonical_series_name=canonical.name,
        moved_files=moved_files,
        already_in_place_files=already_in_place,
        failed_files=failed_files,
        repointed_books=repointed_books,
        skipped_books=skipped_books,
        deleted_series_ids=deleted_series_ids,
    )
