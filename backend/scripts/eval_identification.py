"""Score the identification corpus and print the result (prompts/15 Stage 0).

    python scripts/eval_identification.py                 # offline, per-field table + confusion
    python scripts/eval_identification.py --json          # machine-readable
    python scripts/eval_identification.py --write-baseline # stamp IDENTIFICATION-EVAL.md
    python scripts/eval_identification.py --live [--tag recent]   # real providers + real AI (costs credits)

``pytest -m corpus`` runs the same :func:`score_corpus` and gates on the
baseline; this script is the human-facing view and the ``--live`` spot check.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests.corpus_harness import (  # noqa: E402
    FIELDS,
    load_corpus,
    render_baseline_block,
    score_corpus,
)

_EVAL_MD = _BACKEND.parent / "IDENTIFICATION-EVAL.md"
_BEGIN = "<!-- eval-baseline:begin -->"
_END = "<!-- eval-baseline:end -->"


def _print_table(report) -> None:
    print(f"\ncorpus: {len(report.results)} entries  |  scored: {report.scored}  |  "
          f"skipped offline: {report.skipped_offline}")
    print(f"exact-match (all 4 fields): {report.exact_match:.1%}")
    print(f"fast-path rate:             {report.fast_path_rate:.1%}")
    print(f"auto-organized (>=85) wrong: {report.wrong_auto_organized}")
    print("\nper-field precision")
    print("-" * 32)
    for f in FIELDS:
        print(f"  {f:<15} {report.precision[f]:6.1%}")

    wrong = [r for r in report.results if not r.exact and not r.prediction.skipped_offline]
    if wrong:
        print(f"\nconfusion ({len(wrong)} entries)")
        print("-" * 32)
        for r in wrong:
            tags = ",".join(r.entry.case_tags)
            print(f"  [{r.entry.id}]  ({tags})")
            for wf in r.wrong_fields:
                print(f"      {wf}: got {getattr(r.prediction, wf)!r}  want {getattr(r.entry.answer, wf)!r}")

    unverified = [r for r in report.results if not r.entry.answer.verified]
    if unverified:
        print(f"\n{len(unverified)} answer keys still marked verified=false")


def _write_baseline(report) -> None:
    payload = report.as_baseline_dict(
        corpus_size=len(load_corpus()),
        generated=_dt.date.today().isoformat(),
    )
    block = render_baseline_block(payload)
    text = _EVAL_MD.read_text(encoding="utf-8") if _EVAL_MD.is_file() else _skeleton()
    if _BEGIN in text and _END in text:
        head, rest = text.split(_BEGIN, 1)
        _, tail = rest.split(_END, 1)
        text = head + block + tail
    else:
        text = text.rstrip() + "\n\n## Baseline\n\n" + block + "\n"
    _EVAL_MD.write_text(text, encoding="utf-8")
    print(f"stamped baseline into {_EVAL_MD}:\n{json.dumps(payload, indent=2)}")


def _skeleton() -> str:
    return (
        "# Identification eval\n\n"
        "Ground-truth harness for first-pass identification accuracy "
        "(`prompts/15-identification-accuracy-push.md`, Stage 0).\n\n"
        "Run `pytest -m corpus` to gate; `python scripts/eval_identification.py` for the table.\n\n"
        "## Baseline\n\n"
    )


async def _run(live: bool, tag: str | None, only_recorded: bool) -> None:
    entries = load_corpus()
    if tag:
        entries = [e for e in entries if tag in e.case_tags]
    if only_recorded:
        entries = [e for e in entries if e.recorded_ai is not None]
    return await score_corpus(entries, live=live)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--write-baseline", action="store_true", help="stamp IDENTIFICATION-EVAL.md")
    ap.add_argument("--live", action="store_true", help="real providers + real AI (costs credits)")
    ap.add_argument("--tag", help="only score entries carrying this case_tag")
    ap.add_argument("--only-recorded", action="store_true", help="skip fixtures with no recorded AI answer")
    args = ap.parse_args()

    if args.live:
        print("!! --live hits real metadata providers and the Anthropic API (credits) !!")

    report = asyncio.run(_run(args.live, args.tag, args.only_recorded))

    if args.json:
        print(json.dumps(
            {
                "scored": report.scored,
                "skipped_offline": report.skipped_offline,
                "precision": report.precision,
                "exact_match": report.exact_match,
                "fast_path_rate": report.fast_path_rate,
                "wrong_auto_organized": report.wrong_auto_organized,
            },
            indent=2,
        ))
    else:
        _print_table(report)

    if args.write_baseline:
        _write_baseline(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
