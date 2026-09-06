from app.services.metadata_sanity import clamp_series_number, sane_series_number


def test_no_series_forces_number_to_none() -> None:
    assert sane_series_number(None, 3) is None
    assert sane_series_number("", 3) is None


def test_absurd_numbers_are_dropped_series_kept() -> None:
    assert sane_series_number("Alexis Carew", 301) is None
    assert sane_series_number("Some Series", -1) is None
    assert sane_series_number("Some Series", 0) is None


def test_legitimate_numbers_survive() -> None:
    assert sane_series_number("Discworld", 39) == 39
    assert sane_series_number("Discworld", 39.5) == 39.5
    assert sane_series_number("Edge Case", 50) == 50
    assert sane_series_number("Nothing", None) is None


def test_clamp_records_original_and_only_when_changed() -> None:
    raw: dict = {}
    assert clamp_series_number("Alexis Carew", 301, raw) is None
    assert raw["series_number_clamped"] == 301

    raw2: dict = {}
    assert clamp_series_number("Discworld", 5, raw2) == 5
    assert "series_number_clamped" not in raw2
