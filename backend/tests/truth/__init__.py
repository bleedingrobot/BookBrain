"""Eval-only independent ground-truth sources for the identification corpus.

NOT imported by the app. Used by `scripts/build_truth.py` to triangulate a
known-correct answer for each corpus book from sources the identification
pipeline itself does not consult, so `pytest -m corpus` scores against
something that isn't circular. See ../../../IDENTIFICATION-EVAL.md.
"""
