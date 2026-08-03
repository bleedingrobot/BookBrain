import asyncio

import pytest

from app.providers.convert.calibre import ConversionError, convert_to_epub, is_convertible


def test_is_convertible_accepts_mobi_and_rtf() -> None:
    assert is_convertible("book.mobi") is True
    assert is_convertible("book.MOBI") is True
    assert is_convertible("book.rtf") is True


def test_is_convertible_rejects_other_extensions() -> None:
    assert is_convertible("book.epub") is False
    assert is_convertible("book.kpub") is False
    assert is_convertible("cover.jpg") is False
    assert is_convertible("notes.txt") is False


class _FakeProcess:
    def __init__(self, *, returncode: int, stderr: bytes) -> None:
        self.returncode = returncode
        self._stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"", self._stderr)

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class _HangingProcess:
    returncode: int | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        await asyncio.sleep(10)
        return (b"", b"")

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return -9


async def test_convert_to_epub_returns_output_bytes_on_success(monkeypatch) -> None:
    async def fake_exec(binary, input_path, output_path, **kwargs):
        assert binary == "ebook-convert"
        from pathlib import Path

        Path(output_path).write_bytes(b"fake epub bytes")
        return _FakeProcess(returncode=0, stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    result = await convert_to_epub(b"fake mobi content", source_filename="book.mobi")

    assert result == b"fake epub bytes"


async def test_convert_to_epub_raises_on_nonzero_exit(monkeypatch) -> None:
    async def fake_exec(*args, **kwargs):
        return _FakeProcess(returncode=1, stderr=b"conversion error details")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ConversionError, match="conversion error details"):
        await convert_to_epub(b"bad content", source_filename="book.rtf")


async def test_convert_to_epub_raises_when_binary_missing(monkeypatch) -> None:
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ConversionError, match="not found"):
        await convert_to_epub(b"content", source_filename="book.mobi", binary="nonexistent-binary")


async def test_convert_to_epub_raises_on_timeout(monkeypatch) -> None:
    async def fake_exec(*args, **kwargs):
        return _HangingProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(ConversionError, match="timed out"):
        await convert_to_epub(b"content", source_filename="book.mobi", timeout_seconds=0.05)
