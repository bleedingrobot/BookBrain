import asyncio
import tempfile
from pathlib import Path

_CONVERTIBLE_EXTENSIONS = (".mobi", ".rtf")


class ConversionError(Exception):
    pass


def is_convertible(filename: str) -> bool:
    return filename.lower().endswith(_CONVERTIBLE_EXTENSIONS)


async def convert_to_epub(
    data: bytes,
    *,
    source_filename: str,
    binary: str = "ebook-convert",
    timeout_seconds: int = 120,
) -> bytes:
    """Shells out to Calibre's ebook-convert CLI to turn mobi/rtf bytes into
    an EPUB. Runs entirely through temp files — ebook-convert only operates
    on paths, not stdin/stdout streams."""
    suffix = Path(source_filename).suffix or ".bin"
    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        output_path = Path(tmp_dir) / "output.epub"
        input_path.write_bytes(data)

        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                str(input_path),
                str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ConversionError(f"{binary!r} not found — is Calibre installed and on PATH?") from exc

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ConversionError(f"ebook-convert timed out after {timeout_seconds}s") from exc

        if process.returncode != 0:
            raise ConversionError(f"ebook-convert failed: {stderr.decode(errors='replace')[:500]}")
        if not output_path.exists():
            raise ConversionError("ebook-convert reported success but produced no output file")

        return output_path.read_bytes()
