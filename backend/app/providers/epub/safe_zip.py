import io
import zipfile

from app.providers.epub.errors import EpubParseError, EpubTooLargeError, EpubTooManyEntriesError


class SafeZipReader:
    """SPEC.md §1: zip-bomb guard. Checks declared entry sizes from the
    central directory *before* extracting anything — a hostile archive never
    gets decompressed past the point where it's rejected."""

    def __init__(
        self,
        data: bytes,
        *,
        max_entry_bytes: int,
        max_total_bytes: int,
        max_entries: int,
    ) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self._zip.infolist()
        except zipfile.BadZipFile as exc:
            raise EpubParseError("not a valid zip archive") from exc

        if len(infos) > max_entries:
            raise EpubTooManyEntriesError(
                f"EPUB has {len(infos)} entries, max allowed is {max_entries}"
            )

        total = 0
        for info in infos:
            if info.file_size > max_entry_bytes:
                raise EpubTooLargeError(
                    f"entry {info.filename!r} declares {info.file_size} bytes, "
                    f"max allowed per entry is {max_entry_bytes}"
                )
            total += info.file_size
            if total > max_total_bytes:
                raise EpubTooLargeError(
                    f"EPUB declares more than {max_total_bytes} total decompressed bytes"
                )

        self._names = {info.filename for info in infos}

    def exists(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> bytes:
        try:
            return self._zip.read(name)
        except zipfile.BadZipFile as exc:
            raise EpubParseError(f"could not read {name!r} from archive") from exc
