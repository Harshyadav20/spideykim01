"""Read font family names straight from TTF name tables (no dependencies)."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional

_CACHE: dict[str, Optional[str]] = {}


def font_family(path: str | Path) -> Optional[str]:
    """Best-effort family name for libass: prefers GDI family (ID 1)."""
    p = str(path)
    if p in _CACHE:
        return _CACHE[p]
    name = None
    try:
        data = Path(p).read_bytes()
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_table = None
        for i in range(num_tables):
            off = 12 + 16 * i
            tag = data[off:off + 4]
            if tag == b"name":
                name_table = struct.unpack(">I", data[off + 8:off + 12])[0]
                break
        if name_table is None:
            raise ValueError("no name table")
        count, string_offset = struct.unpack(">HH", data[name_table + 2:name_table + 6])
        records = []
        for i in range(count):
            rec = name_table + 6 + 12 * i
            platform, _, _, name_id, length, offset = struct.unpack(">HHHHHH", data[rec:rec + 12])
            records.append((platform, name_id, length, name_table + string_offset + offset))
        # Prefer Windows (platform 3) family/subfamily-qualified name
        best = None
        typographic = None
        for platform, name_id, length, offset in records:
            if name_id not in (1, 16):
                continue
            raw = data[offset:offset + length]
            if platform == 3:
                val = raw.decode("utf-16-be", "ignore")
            elif platform == 1:
                val = raw.decode("mac-roman", "ignore")
            else:
                continue
            if name_id == 1:
                best = best or val
            if name_id == 16:
                typographic = typographic or val
        name = typographic or best
    except Exception:  # noqa: BLE001
        name = None
    _CACHE[p] = name
    return name
