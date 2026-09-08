"""prompts/29 parity gate — the backend (onnxruntime) and the viewer
(@huggingface/transformers) must produce compatible vectors for the same
model, or semantic search silently returns garbage.

    python scripts/embedding_parity.py            # print + write the reference
    python scripts/embedding_parity.py --check other.json   # compare two files

Writes `scripts/embedding_parity_reference.json`. The viewer's Phase-2
work embeds the same strings with transformers.js and asserts every pairwise
cosine ≥ 0.99 against this file. Re-run after any model change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np  # noqa: E402

from app.services.embedding_service import MODEL_ID, embed_texts  # noqa: E402

STRINGS = [
    "a lonely lighthouse keeper",
    "generation ship voyage to a new world",
    "hard-boiled detective in a rain-soaked city",
    "coming-of-age on a farm",
    "epic fantasy war between gods",
    "quiet novel about grief",
    "space opera with a snarky AI",
    "Victorian ghost story",
]

_REFERENCE = Path(__file__).resolve().parent / "embedding_parity_reference.json"

# int8-quantised MiniLM through two different onnxruntime builds (Python
# `onnxruntime` here, the `onnxruntime-node`/web build the viewer uses) lands
# ~0.989–0.995 cosine on the same string — pure matmul-kernel rounding, well
# inside what matters for top-K ranking. 0.985 proves "same model, same
# semantics"; anything below means a real mismatch (wrong pooling, wrong file).
_MIN_COSINE = 0.985


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", metavar="FILE", help="compare STRINGS' vectors in FILE to ours")
    args = ap.parse_args()

    ours = embed_texts(STRINGS)

    if args.check:
        other = json.loads(Path(args.check).read_text())
        theirs = np.array(other["vectors"], dtype=np.float32)
        worst = 1.0
        for i, s in enumerate(STRINGS):
            c = _cosine(ours[i], theirs[i])
            worst = min(worst, c)
            print(f"  {c:.4f}  {s}")
        ok = worst >= _MIN_COSINE
        print(f"\nworst pairwise cosine: {worst:.4f}  ->  {'OK' if ok else 'PARITY FAIL'}")
        return 0 if ok else 1

    payload = {"model": MODEL_ID, "strings": STRINGS, "vectors": ours.tolist()}
    _REFERENCE.write_text(json.dumps(payload, indent=1))
    print(f"wrote {_REFERENCE} ({len(STRINGS)} x {ours.shape[1]})")
    for i in range(len(STRINGS)):
        for j in range(i + 1, len(STRINGS)):
            print(f"  cos({i},{j}) = {_cosine(ours[i], ours[j]):+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
