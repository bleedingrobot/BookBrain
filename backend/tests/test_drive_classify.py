from app.providers.drive.classify import classify_file, is_supported_ebook


def test_single_parent_is_clean() -> None:
    assert classify_file({"parents": ["abc"]}) is None


def test_multi_parent_flagged() -> None:
    assert classify_file({"parents": ["abc", "def"]}) == "multi_parent"


def test_no_parent_flagged() -> None:
    assert classify_file({"parents": []}) == "no_parent"


def test_missing_parents_key_flagged() -> None:
    assert classify_file({}) == "no_parent"


def test_is_supported_ebook_accepts_epub_kpub_and_cbz() -> None:
    assert is_supported_ebook("book.epub") is True
    assert is_supported_ebook("book.kpub") is True
    assert is_supported_ebook("Book.EPUB") is True
    assert is_supported_ebook("Saga 001.cbz") is True
    assert is_supported_ebook("Saga 001.CBZ") is True


def test_is_supported_ebook_rejects_other_extensions() -> None:
    assert is_supported_ebook("cover.jpg") is False
    assert is_supported_ebook("notes.txt") is False
    assert is_supported_ebook("book.pdf") is False
    assert is_supported_ebook("book.epub.zip") is False
    assert is_supported_ebook("comic.cbr") is False
