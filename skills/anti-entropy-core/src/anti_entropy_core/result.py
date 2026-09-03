from __future__ import annotations

from typing import Any, Iterable

from .constants import ABI
from .model import Issue


def make_result(
    command: str,
    status: str,
    exit_code: int,
    *,
    data: dict[str, Any] | None = None,
    issues: Iterable[Issue | dict[str, Any]] = (),
) -> dict[str, Any]:
    encoded = [item.as_dict() if isinstance(item, Issue) else item for item in issues]
    return {
        "abi": ABI,
        "status": status,
        "exit_code": exit_code,
        "command": command,
        "data": data or {},
        "issues": encoded,
    }


__all__ = ["make_result"]

