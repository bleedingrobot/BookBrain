import asyncio
import subprocess
import tempfile
from pathlib import Path

_CONVERTIBLE_EXTENSIONS = (".mobi", ".rtf")


class ConversionError(Exception):
    pass


def is_convertible(filename: str) -> bool:
    return filename.lower().endswith(_CONVERTIBLE_EXTENSIONS)


def _run_ebook_convert(
    binary: str, input_path: Path, output_path: Path, timeout_seconds: int
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binary, str(input_path), str(output_path)],
        capture_output=True,
        timeout=timeout_seconds,
    )


async def convert_to_epub(
    data: bytes,
    *,
    source_filename: str,
    binary: str = "ebook-convert",
    timeout_seconds: int = 120,
) -> bytes:
    """Shells out to Calibre's ebook-convert CLI to turn mobi/rtf bytes into
    an EPUB. Runs entirely through temp files — ebook-convert only operates
    on paths, not stdin/stdout streams.

    Uses the plain blocking subprocess module via asyncio.to_thread rather
    than asyncio.create_subprocess_exec: the latter requires the loop's
    subprocess transport, which SelectorEventLoop doesn't implement on
    Windows (raises NotImplementedError) — uvicorn can end up running under
    exactly that loop, and by the time our own code runs the loop already
    exists, too late to swap its policy. A thread-pooled blocking call works
    under any event loop on any platform, no policy games required."""
    suffix = Path(source_filename).suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        output_path = Path(tmp_dir) / "output.epub"
        input_path.write_bytes(data)

        try:
            result = await asyncio.to_thread(
                _run_ebook_convert, binary, input_path, output_path, timeout_seconds
            )
        except FileNotFoundError as exc:
            raise ConversionError(f"{binary!r} not found — is Calibre installed and on PATH?") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"ebook-convert timed out after {timeout_seconds}s") from exc

        if result.returncode != 0:
            raise ConversionError(f"ebook-convert failed: {result.stderr.decode(errors='replace')[:500]}")
        if not output_path.exists():
            raise ConversionError("ebook-convert reported success but produced no output file")

        return output_path.read_bytes()
