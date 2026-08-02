from app.providers.drive.classify import classify_file


def test_single_parent_is_clean() -> None:
    assert classify_file({"parents": ["abc"]}) is None


def test_multi_parent_flagged() -> None:
    assert classify_file({"parents": ["abc", "def"]}) == "multi_parent"


def test_no_parent_flagged() -> None:
    assert classify_file({"parents": []}) == "no_parent"


def test_missing_parents_key_flagged() -> None:
    assert classify_file({}) == "no_parent"
