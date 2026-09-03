import subprocess

import pytest

from app.providers.convert.calibre import ConversionError, convert_to_epub, is_convertible


def test_is_convertible_accepts_mobi_rtf_and_txt() -> None:
    assert is_convertible("book.mobi") is True
    assert is_convertible("book.MOBI") is True
    assert is_convertible("book.rtf") is True
    assert is_convertible("book.txt") is True
    assert is_convertible("book.TXT") is True


def test_is_convertible_rejects_other_extensions() -> None:
    assert is_convertible("book.epub") is False
    assert is_convertible("book.kpub") is False
    assert is_convertible("cover.jpg") is False
    assert is_convertible("book.pdf") is False


async def test_convert_to_epub_returns_output_bytes_on_success(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        from pathlib import Path

        Path(cmd[2]).write_bytes(b"fake epub bytes")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = await convert_to_epub(b"fake mobi content", source_filename="book.mobi")

    assert result == b"fake epub bytes"


async def test_convert_to_epub_raises_on_nonzero_exit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"conversion error details")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ConversionError, match="conversion error details"):
        await convert_to_epub(b"bad content", source_filename="book.rtf")


async def test_convert_to_epub_raises_when_binary_missing(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ConversionError, match="not found"):
        await convert_to_epub(b"content", source_filename="book.mobi", binary="nonexistent-binary")


async def test_convert_to_epub_raises_on_timeout(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ConversionError, match="timed out"):
        await convert_to_epub(b"content", source_filename="book.mobi", timeout_seconds=1)
