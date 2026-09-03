from __future__ import annotations

import os
from pathlib import Path


def logical_absolute(path: Path) -> Path:
    """Return an absolute path without Windows' internal extended-path prefix."""
    absolute = path.absolute()
    if os.name != "nt":
        return absolute
    value = str(absolute)
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return absolute


def native_path(path: Path) -> Path:
    """Use Windows extended-path spelling for filesystem operations."""
    logical = logical_absolute(path)
    if os.name != "nt":
        return logical
    value = str(logical)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


__all__ = ["logical_absolute", "native_path"]
