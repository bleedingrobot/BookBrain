"""Unit tests for the ground-truth triangulation (tests/truth/consensus.py).

The corpus answer keys are only as trustworthy as this logic, so it gets its
own tests — not under `-m corpus` (no I/O, always runs).
"""

from tests.truth.consensus import triangulate
from tests.truth.types import TruthClaim


def _c(source, **kw):
    return TruthClaim(source=source, **kw)


def test_two_independent_sources_agree_is_consensus():
    ans = triangulate([
        _c("wikidata", title="Dune", author="Frank Herbert", series="Dune", series_number=1),
        _c("web_claude_identify", title="Dune", author="Frank Herbert", series="Dune", series_number=1),
        _c("epub", title="Dune", author="Frank Herbert"),
    ])
    assert ans.title == "Dune"
    assert ans.provenance == {
        "title": "consensus", "author": "consensus",
        "series": "consensus", "series_number": "consensus",
    }


def test_claude_only_agreement_is_weak():
    ans = triangulate([
        _c("web_claude_identify", title="X", author="Y", series="Z", series_number=2),
        _c("web_claude_verify", title="X", author="Y", series="Z", series_number=2),
    ])
    assert ans.series == "Z"
    assert ans.provenance["series"] == "weak"
    assert ans.provenance["title"] == "weak"


def test_epub_plus_provider_alone_is_not_confirmation():
    # provider data is often copied from the EPUB — two of them is not
    # independent corroboration.
    ans = triangulate([
        _c("epub", title="Junk Title", author="Someone", series="Made Up"),
        _c("provider", title="Junk Title", author="Someone", series="Made Up"),
    ])
    assert ans.provenance["title"] == "unresolved"
    assert ans.provenance["series"] == "unresolved"
    assert ans.title is None


def test_disagreement_is_unresolved_not_a_coin_flip():
    ans = triangulate([
        _c("wikidata", title="Playing with Fire", author="Arthur Conan Doyle"),
        _c("epub", title="Playing with Fire", author="Derek Landy"),
    ])
    assert ans.provenance["author"] == "unresolved"
    assert ans.author is None
    assert any("author" in d for d in ans.disagreements)


def test_wikidata_confirms_an_epub_claim():
    ans = triangulate([
        _c("wikidata", title="City of Bones", author="Martha Wells", series=None),
        _c("epub", title="City of Bones", author="Martha Wells"),
        _c("provider", title="City of Bones", author="Martha Wells"),
    ])
    assert ans.author == "Martha Wells"
    assert ans.provenance["author"] == "consensus"  # wikidata + epub, both independent enough


def test_a_junk_series_string_does_not_swallow_a_real_one():
    ans = triangulate([
        _c("epub", title="Shadowdale",
           series="Forgotten Realms.Avatar Series.Forgotten Realms.1 of 5._.ICB Best"),
        _c("wikidata", title="Shadowdale", author="Scott Ciencin", series="The Avatar Series"),
        _c("web_claude_identify", title="Shadowdale", author="Scott Ciencin", series="The Avatar Series"),
    ])
    assert ans.series == "The Avatar Series"
    assert ans.provenance["series"] == "consensus"


def test_series_name_phrasing_differences_still_agree():
    ans = triangulate([
        _c("wikidata", title="Nevernight", author="Jay Kristoff", series="Nevernight Chronicle", series_number=1),
        _c("web_claude_identify", title="Nevernight", author="Jay Kristoff",
           series="The Nevernight Chronicle", series_number=1),
    ])
    assert ans.provenance["series"] == "consensus"
    assert ans.series_number == 1.0


def test_confirmed_standalone_when_both_grounded_calls_say_so():
    ans = triangulate([
        _c("epub", title="A One-Off", author="Solo Writer"),
        _c("web_claude_identify", title="A One-Off", author="Solo Writer", series=None),
        _c("web_claude_verify", title="A One-Off", author="Solo Writer", series=None),
    ])
    assert ans.series is None
    assert ans.provenance["series"] in ("weak", "consensus")
