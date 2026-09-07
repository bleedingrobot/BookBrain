"""Reading a RAR-packed comic archive (`.cbr`) — the same job SafeZipReader
does for a zip-packed one, but RAR isn't in the Python stdlib, so this
shells out to 7-Zip (which reads RAR4 and RAR5, and needs no separate
`unrar`). Nothing is ever *written* back — a `.cbr` is kept in its original
format exactly like a `.cbz`.

Same zip-bomb discipline as SafeZipReader: the entry sizes are read from
7-Zip's listing and checked against the caps *before* anything is
extracted.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.providers.epub.errors import EpubParseError, EpubTooLargeError, EpubTooManyEntriesError

# Where 7-Zip installs on Windows when it isn't on PATH (it usually isn't).
_WINDOWS_7ZIP_PATHS = (
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
)


def find_seven_zip() -> str | None:
    """The configured 7-Zip binary, or the first one found on PATH, or the
    standard Windows install path — None if there's nothing to use."""
    configured = get_settings().seven_zip_binary
    if configured:
        return configured
    for name in ("7z", "7za", "7zz"):
        found = shutil.which(name)
        if found:
            return found
    for path in _WINDOWS_7ZIP_PATHS:
        if Path(path).is_file():
            return path
    return None


def seven_zip_available() -> bool:
    return find_seven_zip() is not None


def _parse_listing(text: str) -> list[tuple[str, int]]:
    """`7z l -slt` output: a header, a `----------` divider, then one blank-
    line-separated block per entry with `Key = Value` lines. Returns
    (path, uncompressed_size) for the file entries only — directories and
    the leading archive-info block are dropped."""
    text = text.replace("\r\n", "\n")
    _, divider, body = text.partition("\n----------\n")
    if not divider:
        return []

    entries: list[tuple[str, int]] = []
    for block in body.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(" = ")
            if sep:
                fields[key.strip()] = value.strip()

        path = fields.get("Path")
        if not path:
            continue
        # 7-Zip marks directories as Folder = + and/or Attributes = D...
        if fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D"):
            continue
        try:
            size = int(fields.get("Size", "") or 0)
        except ValueError:
            size = 0
        entries.append((path, size))
    return entries


class SafeRarReader:
    """Interface-compatible with SafeZipReader (`names`, `exists`, `read`),
    plus `close()` — call it (or use `with`) so the temp extraction dir is
    cleaned up. Entry names are normalised to forward slashes."""

    def __init__(
        self,
        data: bytes,
        *,
        max_entry_bytes: int,
        max_total_bytes: int,
        max_entries: int,
        timeout_seconds: int | None = None,
    ) -> None:
        tool = find_seven_zip()
        if tool is None:
            raise EpubParseError(
                "7-Zip is needed to read .cbr (RAR) comics but wasn't found — "
                "install it, or set SEVEN_ZIP_BINARY"
            )
        self._tool = tool
        self._timeout = timeout_seconds or get_settings().seven_zip_timeout_seconds

        self._tmp = tempfile.TemporaryDirectory(prefix="bookbrain-cbr-")
        self._archive = Path(self._tmp.name) / "archive.cbr"
        self._archive.write_bytes(data)
        self._extract_root = Path(self._tmp.name) / "extracted"
        self._extracted = False

        try:
            listing = self._run(["l", "-slt", str(self._archive)])
            entries = _parse_listing(listing)
            if not entries:
                raise EpubParseError(
                    "could not read comic archive (empty, encrypted, or not a RAR)"
                )
            if len(entries) > max_entries:
                raise EpubTooManyEntriesError(
                    f"comic archive has {len(entries)} entries, max allowed is {max_entries}"
                )
            total = 0
            self._sizes: dict[str, int] = {}
            for path, size in entries:
                if size > max_entry_bytes:
                    raise EpubTooLargeError(
                        f"entry {path!r} is {size} bytes, max allowed per entry is {max_entry_bytes}"
                    )
                total += size
                if total > max_total_bytes:
                    raise EpubTooLargeError(
                        f"comic archive holds more than {max_total_bytes} total decompressed bytes"
                    )
                self._sizes[path.replace("\\", "/")] = size
            self._names = set(self._sizes)
        except Exception:
            self._tmp.cleanup()
            raise

    def _run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [self._tool, *args],
                capture_output=True,
                timeout=self._timeout,
                stdin=subprocess.DEVNULL,  # so a password prompt fails fast, not hangs
            )
        except FileNotFoundError as exc:
            raise EpubParseError(f"{self._tool!r} not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise EpubParseError(f"7-Zip timed out after {self._timeout}s") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[:300]
            raise EpubParseError(f"7-Zip failed: {detail or 'unknown error'}")
        return result.stdout.decode("utf-8", errors="replace")

    @property
    def names(self) -> list[str]:
        return sorted(self._names)

    def exists(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> bytes:
        if name not in self._names:
            raise EpubParseError(f"{name!r} not in comic archive")
        if not self._extracted:
            # One extraction of the whole archive, reused for every read —
            # the size caps were already enforced from the listing above.
            self._run(["x", f"-o{self._extract_root}", "-y", str(self._archive)])
            self._extracted = True
        target = self._extract_root / Path(name)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise EpubParseError(f"could not read {name!r} after extraction") from exc

    def close(self) -> None:
        self._tmp.cleanup()

    def __enter__(self) -> "SafeRarReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
