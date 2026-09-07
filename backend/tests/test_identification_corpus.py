"""The `-m corpus` gate (prompts/15 Stage 0).

    pytest -m corpus

Runs the hand-verified identification corpus offline and fails if any per-field
precision drops below the baseline recorded in ``IDENTIFICATION-EVAL.md``. The
corpus grows over time, so this never asserts an absolute score — only
"no regression vs the number we last wrote down".

Every later stage of the accuracy push re-runs this and appends its
before/after to ``IDENTIFICATION-EVAL.md``.
"""

import pytest

from tests.corpus_harness import FIELDS, load_baseline, load_corpus, score_corpus

pytestmark = pytest.mark.corpus

# Allow for tiny float noise; a real regression moves a field by whole percent.
_TOLERANCE = 0.005


def _format_table(report) -> str:
    lines = [
        f"corpus: {len(report.results)} entries, {report.scored} scored, "
        f"{report.skipped_offline} skipped offline, {report.skipped_unresolved} unresolved",
        f"exact-match (scorable fields): {report.exact_match:.1%}",
        f"fast-path rate: {report.fast_path_rate:.1%}",
        f"auto-organized (>=85) but wrong: {report.wrong_auto_organized}",
        "",
        "per-field precision (hits / triangulated coverage):",
    ]
    for f in FIELDS:
        lines.append(f"  {f:<15} {report.precision[f]:.1%}  ({report.coverage.get(f, 0)}/{report.scored})")
    wrong = [r for r in report.results if r.field_ok and not r.exact]
    if wrong:
        lines.append("")
        lines.append(f"confusion ({len(wrong)}):")
        for r in wrong:
            for wf in r.wrong_fields:
                lines.append(
                    f"  [{r.entry.id}] {wf}: got {getattr(r.prediction, wf)!r} "
                    f"want {getattr(r.entry.answer, wf)!r}"
                )
    return "\n".join(lines)


async def test_corpus_has_entries():
    corpus = load_corpus()
    assert corpus, "no corpus fixtures in tests/identification_corpus/ — run scripts/snapshot_book.py"


async def test_no_per_field_regression(capsys):
    report = await score_corpus()
    table = _format_table(report)
    with capsys.disabled():
        print("\n" + table + "\n")

    baseline = load_baseline()
    if baseline is None:
        pytest.skip(
            "no baseline in IDENTIFICATION-EVAL.md yet — verify the corpus answer keys, "
            "then `python scripts/eval_identification.py --write-baseline`"
        )

    regressions = []
    for f in FIELDS:
        want = baseline["precision"].get(f)
        if want is None:
            continue
        if report.precision[f] < want - _TOLERANCE:
            regressions.append(f"{f}: {report.precision[f]:.1%} < baseline {want:.1%}")
    assert not regressions, "per-field regression vs IDENTIFICATION-EVAL.md:\n" + "\n".join(regressions)
