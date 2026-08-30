from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path is not None:
            value["path"] = self.path
        if self.details:
            value["details"] = self.details
        return value


@dataclass(frozen=True)
class Inspection:
    path: Path
    private_root_files: tuple[str, ...]
    stem: str | None
    representations: tuple[str, ...]
    source: str | None
    assets: tuple[str, ...]
    issues: tuple[Issue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def data(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "envelope": "knowledge-unit-envelope/v2",
            "valid": self.valid,
            "stem": self.stem,
            "representations": list(self.representations),
            "source": self.source,
            "assets": list(self.assets),
            "private_root_files": list(self.private_root_files),
        }


class RequestError(ValueError):
    pass


class ValidationFailure(ValueError):
    def __init__(self, inspection: Inspection) -> None:
        super().__init__("Knowledge unit does not satisfy Envelope v2")
        self.inspection = inspection


__all__ = ["Inspection", "Issue", "RequestError", "ValidationFailure"]

