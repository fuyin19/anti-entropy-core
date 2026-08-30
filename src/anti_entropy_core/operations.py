from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import COMMANDS, ENVELOPE, NAVIGATION_CONTRACT, VERSION, navigation_bytes
from .envelope import inspect_envelope
from .model import Inspection, RequestError, ValidationFailure
from .paths import logical_absolute, native_path

_REPAIRABLE = {
    ("missing_navigation_guide", "AGENTS.md"),
    ("missing_navigation_guide", "CLAUDE.md"),
    ("missing_support_directory", "assets"),
    ("missing_support_directory", "src"),
    ("missing_empty_marker", "assets/.keep"),
    ("missing_empty_marker", "src/.keep"),
}


def parse_path_request(request: dict[str, Any]) -> tuple[Path, tuple[str, ...]]:
    if set(request) - {"path", "private_root_files"} or "path" not in request:
        raise RequestError("request must contain path and optional private_root_files only")
    raw_path = request["path"]
    if not isinstance(raw_path, str) or not raw_path:
        raise RequestError("path must be a non-empty absolute string")
    path = Path(raw_path)
    if not path.is_absolute():
        raise RequestError("path must be absolute")
    private = request.get("private_root_files", [])
    if private not in ([], ["record.json"]):
        raise RequestError('private_root_files must be [] or ["record.json"]')
    return logical_absolute(path), tuple(private)


def capabilities() -> dict[str, Any]:
    common = {"path": "absolute directory path", "private_root_files": 'optional [] or ["record.json"]'}
    return {
        "version": VERSION,
        "envelope": ENVELOPE,
        "navigation_contract": NAVIGATION_CONTRACT,
        "commands": list(COMMANDS),
        "requests": {
            "capabilities": {},
            "inspect": common,
            "validate": common,
            "repair": common,
            "stage.complete": common,
        },
        "mutation_boundary": {
            "stage_only": True,
            "adds_missing_navigation_and_empty_support_only": True,
            "moves_or_publishes_roots": False,
            "rollback_or_recovery": False,
        },
    }


def validate_unit(path: Path, private_root_files: tuple[str, ...]) -> Inspection:
    inspection = inspect_envelope(path, private_root_files)
    if not inspection.valid:
        raise ValidationFailure(inspection)
    return inspection


def _write_missing(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
    except FileExistsError:
        if not path.is_file() or path.read_bytes() != payload:
            raise


def repair_stage(path: Path, private_root_files: tuple[str, ...]) -> tuple[Inspection, list[str]]:
    before = inspect_envelope(path, private_root_files)
    nonrepairable = [issue for issue in before.issues if (issue.code, issue.path) not in _REPAIRABLE]
    if nonrepairable:
        raise ValidationFailure(
            Inspection(
                before.path,
                before.private_root_files,
                before.stem,
                before.representations,
                before.source,
                before.assets,
                tuple(nonrepairable),
            )
        )

    root = native_path(path)
    changes: list[str] = []
    agents, claude = navigation_bytes()
    missing = {(issue.code, issue.path) for issue in before.issues}
    for name, payload in (("AGENTS.md", agents), ("CLAUDE.md", claude)):
        if ("missing_navigation_guide", name) in missing:
            _write_missing(root / name, payload)
            changes.append(name)
    for name in ("assets", "src"):
        directory = root / name
        if ("missing_support_directory", name) in missing:
            directory.mkdir()
            changes.append(name + "/")
        marker = f"{name}/.keep"
        if ("missing_support_directory", name) in missing or ("missing_empty_marker", marker) in missing:
            if any(directory.iterdir()):
                raise OSError(f"stage changed while completing {name}")
            _write_missing(directory / ".keep", b"")
            changes.append(marker)

    after = inspect_envelope(path, private_root_files)
    if not after.valid:
        raise ValidationFailure(after)
    return after, changes


__all__ = ["capabilities", "parse_path_request", "repair_stage", "validate_unit"]
