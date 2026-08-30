from __future__ import annotations

import os
import stat
from pathlib import Path

from .constants import navigation_bytes
from .model import Inspection, Issue
from .paths import logical_absolute, native_path

_GUIDES = {"AGENTS.md", "CLAUDE.md"}
_CONTROL_FILES = {
    "agents.md",
    "agents.override.md",
    "claude.md",
    "claude.local.md",
    ".cursorrules",
    ".mcp.json",
}
_CONTROL_DIRECTORIES = {".claude", ".cursor"}


def _issue(code: str, message: str, path: str | None = None, **details: object) -> Issue:
    return Issue(code, message, path, details)


def _scan(root: Path, issues: list[Issue]) -> tuple[dict[str, os.stat_result], dict[str, os.stat_result]]:
    files: dict[str, os.stat_result] = {}
    directories: dict[str, os.stat_result] = {}

    def visit(directory: Path, prefix: str) -> None:
        entries = sorted(os.scandir(directory), key=lambda item: (item.name.casefold(), item.name))
        folded: dict[str, str] = {}
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            key = entry.name.casefold()
            if key in folded:
                issues.append(
                    _issue(
                        "name_collision",
                        "Sibling names collide under case folding",
                        relative,
                        names=[folded[key], entry.name],
                    )
                )
            else:
                folded[key] = entry.name
            if entry.is_symlink():
                issues.append(_issue("link_not_allowed", "Links are not valid envelope entries", relative))
                continue
            value = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(value.st_mode):
                directories[relative] = value
                visit(Path(entry.path), relative)
            elif stat.S_ISREG(value.st_mode):
                files[relative] = value
            else:
                issues.append(
                    _issue("non_regular_entry", "Envelope entries must be ordinary files or directories", relative)
                )

    visit(root, "")
    return files, directories


def _check_instruction_paths(files: dict[str, os.stat_result], directories: dict[str, os.stat_result], issues: list[Issue]) -> None:
    for relative in sorted((*files, *directories)):
        parts = relative.split("/")
        for index, component in enumerate(parts):
            folded = component.casefold()
            location = "/".join(parts[: index + 1])
            if folded in _CONTROL_DIRECTORIES:
                issues.append(
                    _issue("instruction_control_path", "Instruction-control directories are not knowledge data", location)
                )
            if folded in _CONTROL_FILES and not (len(parts) == 1 and component in _GUIDES):
                issues.append(
                    _issue("instruction_control_path", "Instruction-control files are not knowledge data", location)
                )


def _validate_assets(
    root: Path,
    files: dict[str, os.stat_result],
    directories: dict[str, os.stat_result],
    issues: list[Issue],
) -> tuple[str, ...]:
    if "assets" in files:
        issues.append(_issue("support_not_directory", "assets must be a directory", "assets"))
        return ()
    if "assets" not in directories:
        issues.append(_issue("missing_support_directory", "assets directory is required", "assets"))
        return ()
    descendants = sorted(name for name in (*files, *directories) if name.startswith("assets/"))
    if not descendants:
        issues.append(_issue("missing_empty_marker", "Empty assets must contain zero-byte .keep", "assets/.keep"))
        return ()
    if "assets/.keep" in files:
        if descendants != ["assets/.keep"] or files["assets/.keep"].st_size != 0:
            issues.append(
                _issue("invalid_empty_marker", "assets/.keep is valid only as the sole zero-byte descendant", "assets/.keep")
            )
        return ()
    for directory in sorted(name for name in directories if name.startswith("assets/")):
        prefix = directory + "/"
        if not any(name.startswith(prefix) for name in (*files, *directories)):
            issues.append(_issue("empty_asset_directory", "Nested empty asset directories are invalid", directory))
    return tuple(name for name in sorted(files) if name.startswith("assets/") and name != "assets/.keep")


def _validate_src(
    files: dict[str, os.stat_result],
    directories: dict[str, os.stat_result],
    issues: list[Issue],
) -> str | None:
    if "src" in files:
        issues.append(_issue("support_not_directory", "src must be a directory", "src"))
        return None
    if "src" not in directories:
        issues.append(_issue("missing_support_directory", "src directory is required", "src"))
        return None
    nested = sorted(
        name
        for name in (*files, *directories)
        if name.startswith("src/") and name.count("/") > 1
    )
    if nested:
        issues.append(_issue("invalid_source_directory", "src permits one direct ordinary file only", nested[0]))
    direct = sorted(name for name in files if name.startswith("src/") and name.count("/") == 1)
    direct_dirs = sorted(name for name in directories if name.startswith("src/") and name.count("/") == 1)
    if direct_dirs:
        issues.append(_issue("invalid_source_directory", "src cannot contain directories", direct_dirs[0]))
    if not direct:
        issues.append(_issue("missing_empty_marker", "Empty src must contain zero-byte .keep", "src/.keep"))
        return None
    if len(direct) != 1:
        issues.append(_issue("invalid_source_directory", "src permits exactly one source file or .keep", "src"))
        return None
    source = direct[0]
    if source == "src/.keep":
        if files[source].st_size != 0:
            issues.append(_issue("invalid_empty_marker", "src/.keep must be zero-byte", source))
        return None
    return source


def inspect_envelope(path: Path, private_root_files: tuple[str, ...]) -> Inspection:
    logical_root = logical_absolute(path)
    root = native_path(logical_root)
    issues: list[Issue] = []
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return Inspection(
            logical_root,
            private_root_files,
            None,
            (),
            None,
            (),
            (_issue("root_directory_required", "Knowledge-unit root does not exist", str(logical_root)),),
        )
    if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
        return Inspection(
            logical_root,
            private_root_files,
            None,
            (),
            None,
            (),
            (_issue("root_directory_required", "Knowledge-unit root must be an ordinary directory", str(logical_root)),),
        )

    files, directories = _scan(root, issues)
    _check_instruction_paths(files, directories, issues)
    unknown_root_dirs = sorted(name for name in directories if "/" not in name and name not in {"assets", "src"})
    for name in unknown_root_dirs:
        issues.append(_issue("unexpected_root_directory", "Only assets and src are valid root directories", name))

    agents, claude = navigation_bytes()
    for name, expected in (("AGENTS.md", agents), ("CLAUDE.md", claude)):
        if name not in files:
            issues.append(_issue("missing_navigation_guide", "Exact navigation file is required", name))
        elif (root / name).read_bytes() != expected:
            issues.append(_issue("navigation_guide_mismatch", "Navigation file bytes do not match the contract", name))

    record_present = "record.json" in files
    record_declared = private_root_files == ("record.json",)
    if record_present and not record_declared:
        issues.append(_issue("undeclared_private_root_file", "record.json must be declared as a private root file", "record.json"))
    if record_declared and not record_present:
        issues.append(_issue("missing_private_root_file", "Declared record.json is missing", "record.json"))

    excluded = _GUIDES | {"record.json", "assets", "src"}
    representations = tuple(sorted(name for name in files if "/" not in name and name not in excluded))
    stem: str | None = None
    if not representations:
        issues.append(_issue("missing_representation", "At least one root representation is required"))
    else:
        stems: set[str] = set()
        suffixes: dict[str, str] = {}
        for name in representations:
            item = Path(name)
            if not item.suffix or not item.stem:
                issues.append(_issue("representation_extension_required", "Representations require a stem and extension", name))
            else:
                stems.add(item.stem)
                folded = item.suffix.casefold()
                if folded in suffixes:
                    issues.append(
                        _issue(
                            "representation_extension_collision",
                            "Representation extensions collide under case folding",
                            name,
                            extensions=[suffixes[folded], item.suffix],
                        )
                    )
                else:
                    suffixes[folded] = item.suffix
        if len(stems) == 1:
            stem = next(iter(stems))
        else:
            issues.append(_issue("representation_stem_mismatch", "Root representations must share one exact stem"))

    assets = _validate_assets(root, files, directories, issues)
    source = _validate_src(files, directories, issues)
    ordered = tuple(sorted(issues, key=lambda item: (item.path or "", item.code, item.message)))
    return Inspection(logical_root, private_root_files, stem, representations, source, assets, ordered)


__all__ = ["inspect_envelope"]
