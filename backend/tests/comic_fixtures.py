"""A minimal RAR4 archive writer, just for tests — Python has no stdlib RAR
support and 7-Zip can read RAR but not create it. Only the "stored"
(uncompressed) method is emitted, which is all a test fixture needs; 7-Zip
reads these back fine.

RAR4 block layout (little-endian): each block is
    HEAD_CRC(2) HEAD_TYPE(1) HEAD_FLAGS(2) HEAD_SIZE(2) <block-specific...>
where HEAD_CRC is the low 16 bits of crc32 over everything from HEAD_TYPE on.
"""

import struct
import zlib

_MARKER = bytes.fromhex("526172211a0700")  # "Rar!\x1a\x07\x00"


def _block(head_type: int, head_flags: int, body: bytes, add_data: bytes = b"") -> bytes:
    head_size = 7 + len(body)
    header = struct.pack("<BHH", head_type, head_flags, head_size) + body
    crc16 = zlib.crc32(header) & 0xFFFF
    return struct.pack("<H", crc16) + header + add_data


def make_stored_rar(files: dict[str, bytes]) -> bytes:
    out = bytearray(_MARKER)
    # Main archive header (0x73): HighPosAV(2) + PosAV(4), both zero.
    out += _block(0x73, 0x0000, struct.pack("<HI", 0, 0))
    for name, data in files.items():
        name_bytes = name.encode("utf-8")
        body = struct.pack("<II", len(data), len(data))  # PACK_SIZE, UNP_SIZE
        body += struct.pack("<B", 0)  # HOST_OS
        body += struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF)  # FILE_CRC
        body += struct.pack("<I", 0)  # FTIME (MS-DOS, unset)
        body += struct.pack("<B", 20)  # UNP_VER (2.0)
        body += struct.pack("<B", 0x30)  # METHOD 0x30 = stored
        body += struct.pack("<H", len(name_bytes))  # NAME_SIZE
        body += struct.pack("<I", 0x20)  # ATTR (archive bit)
        body += name_bytes
        # flag 0x8000 = block is followed by ADD_SIZE bytes of file data.
        out += _block(0x74, 0x8000, body, add_data=data)
    return bytes(out)
