"""Capture a real book into the identification eval corpus (prompts/15 Stage 0).

Writes one ``tests/identification_corpus/<id>.json`` fixture: the deterministic
EPUB evidence, the metadata-provider candidates, the recorded AI response (if
any), and a *draft* known-correct answer key for a human to verify.

No AI call is made unless you pass ``--with-ai`` to the ``from-drive`` mode.

Modes
-----
    # reconstruct from what a past scan already stored (the usual path — the
    # library has thousands of already-identified books):
    python scripts/snapshot_book.py from-db 1874 --tag ai-series --tag omnibus

    # batch: a JSON spec [{ "file_id": 1874, "id": "...", "case_tags": [...],
    #                       "notes": "..." }, ...] — the checked-in corpus was
    #   built from tests/identification_corpus/_corpus_spec.json
    python scripts/snapshot_book.py from-spec tests/identification_corpus/_corpus_spec.json

    # capture a brand-new file straight from Drive (re-parses, re-queries
    # providers; --with-ai also records a fresh identify_book call — costs
    # credits):
    python scripts/snapshot_book.py from-drive <drive_file_id> --with-ai

The DB is opened read-only, so this is safe to run while the dev server is up.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.providers.metadata.types import MetadataCandidate  # noqa: E402
from app.services.text_match import normalize, normalize_title  # noqa: E402

CORPUS_DIR = _BACKEND / "tests" / "identification_corpus"
_SNIPPET_CAP = 500
_DETERMINISTIC_MODELS = {None, "deterministic", "unavailable", "library_rule", "frozen"}


# --------------------------------------------------------------------------
# read-only DB helpers
# --------------------------------------------------------------------------


def _db_path() -> Path:
    url = get_settings().database_url
    # sqlite+aiosqlite:///./epub_librarian.db  ->  ./epub_librarian.db
    rel = url.split(":///", 1)[1]
    return (_BACKEND / rel).resolve()


def _connect_ro() -> sqlite3.Connection:
    path = _db_path()
    if not path.is_file():
        sys.exit(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _slug(*parts: str) -> str:
    text = " ".join(p for p in parts if p)
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:70] or "book"


# --------------------------------------------------------------------------
# from-db: rebuild a fixture from stored metadata_sources + book_candidates
# --------------------------------------------------------------------------


def _evidence_from_rows(rows: list[sqlite3.Row]) -> dict:
    """Inverse of scan_service._evidence_to_metadata_sources — same shape as
    reident_audit_service.evidence_from_sources, kept faithful to it."""
    by_field: dict[str, str] = {}
    for r in rows:
        if r["source"] in ("epub", "comic"):
            by_field.setdefault(r["field_name"], r["value"])
    num = None
    if by_field.get("series_number"):
        try:
            num = float(by_field["series_number"])
        except ValueError:
            num = None
    return {
        "title": by_field.get("title"),
        "authors": by_field["authors"].split(", ") if by_field.get("authors") else [],
        "language": by_field.get("language"),
        "description": by_field.get("description"),
        "isbn10": by_field.get("isbn10"),
        "isbn13": by_field.get("isbn13"),
        "series": by_field.get("series"),
        "series_number": num,
        "text_snippet": (by_field.get("text_snippet") or "")[:_SNIPPET_CAP],
    }


def _candidates_from_rows(rows: list[sqlite3.Row]) -> list[dict]:
    return [
        {
            "title": r["title"],
            "authors": r["author"].split(", ") if r["author"] else [],
            "series": r["series"],
            "series_number": r["series_number"],
            "source": r["source"],
        }
        for r in rows
    ]


def _dig_tool_input(raw_response: dict | None, tool_name: str) -> dict | None:
    if not raw_response:
        return None
    for block in raw_response.get("content", []) or []:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("input")
    return None


def _recorded_ai(raw_response: dict | None, model: str | None) -> dict | None:
    if model in _DETERMINISTIC_MODELS:
        return None
    return _dig_tool_input(raw_response, "identify_book")


def _recorded_series_ai(raw_response: dict | None) -> dict | None:
    if not raw_response:
        return None
    lookup = raw_response.get("series_lookup")
    return _dig_tool_input(lookup, "identify_series") if isinstance(lookup, dict) else None


def _reconstruct_isbn_on_candidates(
    evidence: dict, candidates: list[dict], model: str | None
) -> str:
    """book_candidates rows don't store ISBNs. When we *know* the deterministic
    fast path fired (model == 'deterministic'), the matched candidate's ISBN
    equalled the EPUB's — so put the EPUB ISBN back onto the candidate whose
    title+author agree, which is exactly _find_isbn_match's condition. Returns
    a fidelity tag."""
    isbn13, isbn10 = evidence.get("isbn13"), evidence.get("isbn10")
    if model != "deterministic" or not (isbn13 or isbn10) or not evidence.get("title"):
        return "db-reconstructed"
    ev_title = normalize_title(evidence["title"])
    ev_author = normalize(evidence["authors"][0]) if evidence.get("authors") else ""
    patched = False
    for c in candidates:
        if normalize_title(c.get("title")) != ev_title:
            continue
        if ev_author and c.get("authors") and normalize(c["authors"][0]) != ev_author:
            continue
        c["isbn13"], c["isbn10"] = isbn13, isbn10
        patched = True
    return "db-reconstructed+isbn-refit" if patched else "db-reconstructed"


def _draft_answer(conn: sqlite3.Connection, file_id: int) -> dict:
    corrected = conn.execute(
        "select correction_json from reviews where file_id=? and status='corrected' "
        "order by id desc limit 1",
        (file_id,),
    ).fetchone()
    if corrected and corrected["correction_json"]:
        cj = json.loads(corrected["correction_json"])
        return {
            "title": cj.get("title"),
            "author": cj.get("author"),
            "series": cj.get("series"),
            "series_number": cj.get("series_number"),
            "verified": True,
            "source": "human_correction",
        }
    row = conn.execute(
        """select b.canonical_title, b.series_number, au.name author, se.name series
           from files f join books b on b.id = f.book_id
           left join authors au on au.id = b.author_id
           left join series se on se.id = b.series_id
           where f.id = ?""",
        (file_id,),
    ).fetchone()
    if not row:
        sys.exit(f"file {file_id} has no resolved book")
    return {
        "title": row["canonical_title"],
        "author": row["author"],
        "series": row["series"],
        "series_number": row["series_number"],
        "verified": False,
        "source": "stored_identification",
    }


def snapshot_from_db(
    conn: sqlite3.Connection,
    file_id: int,
    *,
    entry_id: str | None,
    case_tags: list[str],
    notes: str,
) -> dict:
    f = conn.execute("select id, drive_file_id, filename from files where id=?", (file_id,)).fetchone()
    if not f:
        sys.exit(f"no file with id {file_id}")

    src_rows = conn.execute(
        "select field_name, value, source from metadata_sources where file_id=?", (file_id,)
    ).fetchall()
    orig_filename = next(
        (r["value"] for r in src_rows if r["field_name"] == "filename" and r["source"] == "drive"),
        f["filename"],
    )
    evidence = _evidence_from_rows(src_rows)
    candidates = _candidates_from_rows(
        conn.execute(
            "select title, author, series, series_number, source from book_candidates "
            "where file_id=? and source != 'filename'",  # Stage C: identify() re-parses the name
            (file_id,),
        ).fetchall()
    )
    decision = conn.execute(
        "select model, raw_response_json, computed_confidence from ai_decisions "
        "where file_id=? order by id desc limit 1",
        (file_id,),
    ).fetchone()
    model = decision["model"] if decision else None
    raw = json.loads(decision["raw_response_json"]) if decision and decision["raw_response_json"] else None

    fidelity = _reconstruct_isbn_on_candidates(evidence, candidates, model)
    answer = _draft_answer(conn, file_id)

    return {
        "id": entry_id or _slug(answer.get("author") or "", answer["title"] or f["filename"]),
        "case_tags": case_tags,
        "notes": notes,
        "source": {
            "file_id": file_id,
            "drive_file_id": f["drive_file_id"],
            "filename": orig_filename,
            "captured_from": "db",
            "stored_model": model,
            "stored_confidence": decision["computed_confidence"] if decision else None,
        },
        "filename": orig_filename,
        "evidence": evidence,
        "candidates": candidates,
        "candidate_fidelity": fidelity,
        "recorded_ai": _recorded_ai(raw, model),
        "recorded_series_ai": _recorded_series_ai(raw),
        "answer": answer,
    }


# --------------------------------------------------------------------------
# from-drive: re-parse + re-query providers live
# --------------------------------------------------------------------------


async def snapshot_from_drive(
    drive_file_id: str, *, entry_id: str | None, case_tags: list[str], notes: str, with_ai: bool
) -> dict:
    from app.data.db import async_session_factory
    from app.data.repositories.settings_repository import SettingsRepository
    from app.providers.drive.client import build_drive_service
    from app.providers.drive.provider import DriveProvider
    from app.providers.epub.parser import parse_epub_safely
    from app.services.auth_service import get_auth_service
    from app.services.candidate_service import default_candidate_service

    settings = get_settings()
    async with async_session_factory() as session:
        creds = await get_auth_service().get_credentials(SettingsRepository(session))
    if creds is None:
        sys.exit("Google Drive is not connected — reconnect in the app first.")

    provider = DriveProvider(build_drive_service(creds))
    meta = await asyncio.to_thread(
        lambda: provider._service.files()
        .get(fileId=drive_file_id, fields="id,name")
        .execute()
    )
    data = await asyncio.to_thread(provider.download_file, drive_file_id)
    ev = parse_epub_safely(
        data,
        max_entry_bytes=settings.epub_max_entry_bytes,
        max_total_bytes=settings.epub_max_total_bytes,
        max_entries=settings.epub_max_entries,
        timeout_seconds=settings.epub_parse_timeout_seconds,
    )
    cands = await default_candidate_service().generate_candidates(
        isbn13=ev.isbn13,
        isbn10=ev.isbn10,
        title=ev.title,
        authors=", ".join(ev.authors) if ev.authors else None,
    )

    recorded_ai = None
    if with_ai:
        from app.services.identification_service import _build_prompt
        from app.providers.ai.anthropic_client import AnthropicIdentificationClient

        _result, raw = await AnthropicIdentificationClient().identify(
            _build_prompt(meta["name"], ev, cands, None)
        )
        for block in raw.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "identify_book":
                recorded_ai = block["input"]

    return {
        "id": entry_id or _slug(", ".join(ev.authors), ev.title or meta["name"]),
        "case_tags": case_tags,
        "notes": notes,
        "source": {
            "drive_file_id": drive_file_id,
            "filename": meta["name"],
            "captured_from": "drive",
        },
        "filename": meta["name"],
        "evidence": {
            "title": ev.title,
            "authors": ev.authors,
            "language": ev.language,
            "description": ev.description,
            "isbn10": ev.isbn10,
            "isbn13": ev.isbn13,
            "series": ev.series,
            "series_number": ev.series_number,
            "text_snippet": ev.text_snippet[:_SNIPPET_CAP],
        },
        "candidates": [_candidate_to_dict(c) for c in cands],
        "candidate_fidelity": "fresh",
        "recorded_ai": recorded_ai,
        "recorded_series_ai": None,
        "answer": {
            "title": ev.title,
            "author": ev.authors[0] if ev.authors else None,
            "series": ev.series,
            "series_number": ev.series_number,
            "verified": False,
            "source": "manual",
        },
    }


def _candidate_to_dict(c: MetadataCandidate) -> dict:
    return {
        "title": c.title,
        "authors": c.authors,
        "series": c.series,
        "series_number": c.series_number,
        "genre": c.genre,
        "description": c.description,
        "language": c.language,
        "first_published": c.first_published,
        "isbn13": c.isbn13,
        "isbn10": c.isbn10,
        "source": c.source,
    }


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------


def _write(fixture: dict) -> Path:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    path = CORPUS_DIR / f"{fixture['id']}.json"
    path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_db = sub.add_parser("from-db", help="rebuild a fixture from a scanned file's stored evidence")
    p_db.add_argument("file_id", type=int)
    p_db.add_argument("--id", dest="entry_id")
    p_db.add_argument("--tag", dest="tags", action="append", default=[])
    p_db.add_argument("--note", default="")

    p_spec = sub.add_parser("from-spec", help="batch from-db from a JSON spec list")
    p_spec.add_argument("spec", type=Path)

    p_drive = sub.add_parser("from-drive", help="capture a new file live from Drive")
    p_drive.add_argument("drive_file_id")
    p_drive.add_argument("--id", dest="entry_id")
    p_drive.add_argument("--tag", dest="tags", action="append", default=[])
    p_drive.add_argument("--note", default="")
    p_drive.add_argument("--with-ai", action="store_true", help="also record a real identify_book call")

    args = parser.parse_args()

    if args.mode == "from-db":
        with _connect_ro() as conn:
            fx = snapshot_from_db(
                conn, args.file_id, entry_id=args.entry_id, case_tags=args.tags, notes=args.note
            )
        print(f"wrote {_write(fx)}  (answer verified={fx['answer']['verified']})")
        return 0

    if args.mode == "from-spec":
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        written = 0
        with _connect_ro() as conn:
            for item in spec:
                fx = snapshot_from_db(
                    conn,
                    int(item["file_id"]),
                    entry_id=item.get("id"),
                    case_tags=list(item.get("case_tags", [])),
                    notes=item.get("notes", ""),
                )
                _write(fx)
                written += 1
        print(f"wrote {written} fixtures to {CORPUS_DIR}")
        return 0

    if args.mode == "from-drive":
        fx = asyncio.run(
            snapshot_from_drive(
                args.drive_file_id,
                entry_id=args.entry_id,
                case_tags=args.tags,
                notes=args.note,
                with_ai=args.with_ai,
            )
        )
        print(f"wrote {_write(fx)}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
