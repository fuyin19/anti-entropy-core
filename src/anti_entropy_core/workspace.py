from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .constants import (
    AGENT_WORKBENCH_CONTRACT,
    COLLABORATIVE_WORKSPACE_COMMANDS,
    COLLABORATIVE_WORKSPACE_CONTRACT,
    VERSION,
    WORKSPACE_CONTRACTS,
    workspace_navigation_bytes,
)
from .envelope import inspect_envelope
from .model import Issue, RequestError, ValidationFailure, WorkspaceInspection
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
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_INVALID_WINDOWS_CHARACTERS = set('<>:"\\|?*')
_SHA256_LENGTH = 64
_SOURCE_KINDS = {"file", "knowledge_unit"}
_PROVIDER_ROUTES = {"knowledge-unit-copy", "file-conversion", "markdown-conversion"}
_QUALITIES = {"ready", "ready_with_warnings"}
_OUTDATED = "_outdated"
_OUTDATED_BATCH = re.compile(r"generation-([1-9][0-9]*)-([0-9]{8}T[0-9]{4}Z)\Z")
_OUTER_ROLES = {"reference": "ref", "agent_workbench": "agent-workbench"}
_OUTER_MANIFEST_KEYS = {"contract", "workspace_id", "roles"}
_INNER_MANIFEST_KEYS = {
    "contract",
    "workspace_id",
    "generation",
    "quality",
    "source_records",
    "source_tree_digest",
    "items",
    "warnings",
}
_SOURCE_RECORD_KEYS = {"path", "kind", "digest"}
_ITEM_KEYS = {
    "source_path",
    "source_kind",
    "source_digest",
    "unit_path",
    "prepared_digest",
    "provider_route",
    "quality",
    "issues",
}
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class _DuplicateKey(ValueError):
    pass


def _issue(code: str, message: str, path: str | None = None, **details: object) -> Issue:
    return Issue(code, message, path, details)


def _is_linklike(value: os.stat_result, *, symlink: bool = False) -> bool:
    return symlink or bool(getattr(value, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)


def _ordered(issues: Iterable[Issue]) -> tuple[Issue, ...]:
    return tuple(sorted(issues, key=lambda item: (item.path or "", item.code, item.message)))


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON member: {key}")
        result[key] = value
    return result


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def source_records_digest(records: list[dict[str, str]]) -> str:
    return hashlib.sha256(_canonical_json(records)).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_tree_digest(path: Path) -> str:
    root = native_path(path)
    records: list[dict[str, str]] = []

    def visit(directory: Path, prefix: str) -> None:
        entries = sorted(os.scandir(directory), key=lambda item: item.name)
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            value = entry.stat(follow_symlinks=False)
            if _is_linklike(value, symlink=entry.is_symlink()):
                raise OSError(f"link-like entry cannot be digested: {relative}")
            if stat.S_ISDIR(value.st_mode):
                records.append({"kind": "directory", "path": relative})
                visit(Path(entry.path), relative)
            elif stat.S_ISREG(value.st_mode):
                records.append({"digest": _file_digest(Path(entry.path)), "kind": "file", "path": relative})
            else:
                raise OSError(f"non-regular entry cannot be digested: {relative}")

    visit(root, "")
    records.sort(key=lambda item: item["path"])
    return hashlib.sha256(_canonical_json(records)).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _component_error(component: str) -> str | None:
    if not component or component in {".", ".."}:
        return "empty, dot, and dot-dot path components are not allowed"
    if unicodedata.normalize("NFC", component) != component:
        return "path components must use Unicode NFC"
    if component.endswith((".", " ")):
        return "path components cannot end with a dot or space"
    if any(
        ord(character) < 32 or ord(character) == 127 or character in _INVALID_WINDOWS_CHARACTERS
        for character in component
    ):
        return "path components contain a control or Windows-invalid character"
    device = component.split(".", 1)[0].upper()
    if device in _WINDOWS_RESERVED:
        return "Windows reserved device names are not allowed"
    return None


def _validate_relative_path(value: object, field: str, issues: list[Issue]) -> str | None:
    if not isinstance(value, str) or not value:
        issues.append(_issue("invalid_relative_path", "Path must be a non-empty string", field))
        return None
    if value.startswith("/") or "\\" in value or unicodedata.normalize("NFC", value) != value:
        issues.append(_issue("invalid_relative_path", "Path must be a normalized relative POSIX path", field))
        return None
    parts = value.split("/")
    for component in parts:
        message = _component_error(component)
        if message is not None:
            issues.append(_issue("invalid_relative_path", message, field, value=value))
            return None
    return value


def _validate_path_set(values: list[str], field: str, issues: list[Issue]) -> None:
    seen: dict[str, str] = {}
    for value in values:
        key = _path_key(value)
        if key in seen:
            issues.append(
                _issue(
                    "path_collision",
                    "Paths collide under Unicode normalization and case folding",
                    field,
                    paths=[seen[key], value],
                )
            )
        else:
            seen[key] = value
    ordered = sorted(values, key=lambda item: (item.count("/"), _path_key(item), item))
    for index, left in enumerate(ordered):
        prefix = _path_key(left) + "/"
        for right in ordered[index + 1 :]:
            if _path_key(right).startswith(prefix):
                issues.append(
                    _issue(
                        "path_prefix_collision",
                        "A source item cannot contain another source item",
                        field,
                        paths=[left, right],
                    )
                )


def _validate_instruction_path(value: str, kind: str, field: str, issues: list[Issue]) -> None:
    parts = value.split("/")
    for index, component in enumerate(parts):
        terminal = index == len(parts) - 1
        is_directory = not terminal or kind == "knowledge_unit"
        folded = component.casefold()
        if (is_directory and folded in _CONTROL_DIRECTORIES) or (not is_directory and folded in _CONTROL_FILES):
            issues.append(
                _issue(
                    "instruction_control_path",
                    "Instruction-control paths are not workspace data",
                    field,
                    value=value,
                )
            )
            return


def _canonical_uuid4(value: object, field: str, issues: list[Issue]) -> str | None:
    if not isinstance(value, str):
        issues.append(_issue("invalid_workspace_id", "workspace_id must be a canonical UUIDv4 string", field))
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        parsed = None
    if parsed is None or parsed.version != 4 or str(parsed) != value:
        issues.append(_issue("invalid_workspace_id", "workspace_id must be a canonical lowercase UUIDv4 string", field))
        return None
    return value


def _load_manifest(path: Path, relative: str, issues: list[Issue]) -> dict[str, Any] | None:
    try:
        value_stat = path.lstat()
    except FileNotFoundError:
        issues.append(_issue("missing_manifest", "Exact workspace manifest is required", relative))
        return None
    if _is_linklike(value_stat, symlink=path.is_symlink()) or not stat.S_ISREG(value_stat.st_mode):
        issues.append(_issue("manifest_not_file", "Workspace manifest must be an ordinary file", relative))
        return None
    try:
        decoded = path.read_bytes().decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_duplicate_safe_object)
    except (UnicodeError, json.JSONDecodeError, _DuplicateKey) as exc:
        issues.append(_issue("invalid_manifest_json", f"Manifest must be unambiguous UTF-8 JSON ({type(exc).__name__})", relative))
        return None
    if not isinstance(value, dict):
        issues.append(_issue("invalid_manifest_schema", "Manifest root must be an object", relative))
        return None
    return value


def _check_exact_keys(value: dict[str, Any], expected: set[str], relative: str, issues: list[Issue]) -> bool:
    if set(value) == expected:
        return True
    issues.append(
        _issue(
            "invalid_manifest_schema",
            "Manifest fields do not match the contract",
            relative,
            missing=sorted(expected - set(value)),
            unexpected=sorted(set(value) - expected),
        )
    )
    return False


def _root_state(path: Path, contract: str) -> tuple[Path, Path, list[Issue]]:
    logical_root = logical_absolute(path)
    root = native_path(logical_root)
    issues: list[Issue] = []
    try:
        value = root.lstat()
    except FileNotFoundError:
        issues.append(_issue("root_directory_required", "Contract root does not exist", str(logical_root)))
        return logical_root, root, issues
    if _is_linklike(value, symlink=root.is_symlink()) or not stat.S_ISDIR(value.st_mode):
        issues.append(_issue("root_directory_required", "Contract root must be an ordinary directory", str(logical_root)))
    return logical_root, root, issues


def _entry_map(root: Path, issues: list[Issue]) -> dict[str, tuple[Path, os.stat_result, bool]]:
    result: dict[str, tuple[Path, os.stat_result, bool]] = {}
    folded: dict[str, str] = {}
    for entry in sorted(os.scandir(root), key=lambda item: (item.name.casefold(), item.name)):
        relative = entry.name
        key = _path_key(relative)
        if key in folded:
            issues.append(
                _issue("name_collision", "Sibling names collide under Unicode normalization and case folding", relative, names=[folded[key], relative])
            )
        else:
            folded[key] = relative
        message = _component_error(relative)
        if message is not None:
            issues.append(_issue("invalid_entry_name", message, relative))
        value = entry.stat(follow_symlinks=False)
        linklike = _is_linklike(value, symlink=entry.is_symlink())
        if linklike:
            issues.append(_issue("link_not_allowed", "Links and reparse points are not valid workspace entries", relative))
        elif not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode)):
            issues.append(_issue("non_regular_entry", "Workspace entries must be ordinary files or directories", relative))
        result[relative] = (Path(entry.path), value, linklike)
    return result


def _check_navigation(root: Path, contract: str, entries: dict[str, tuple[Path, os.stat_result, bool]], issues: list[Issue]) -> None:
    agents, claude = workspace_navigation_bytes(contract)
    for name, expected in (("AGENTS.md", agents), ("CLAUDE.md", claude)):
        entry = entries.get(name)
        if entry is None:
            issues.append(_issue("missing_navigation_guide", "Exact navigation file is required", name))
        elif entry[2] or not stat.S_ISREG(entry[1].st_mode):
            issues.append(_issue("navigation_guide_not_file", "Navigation guide must be an ordinary file", name))
        elif (root / name).read_bytes() != expected:
            issues.append(_issue("navigation_guide_mismatch", "Navigation file bytes do not match the contract", name))


def _check_role_directory(entries: dict[str, tuple[Path, os.stat_result, bool]], name: str, issues: list[Issue]) -> bool:
    entry = entries.get(name)
    if entry is None:
        issues.append(_issue("missing_role_directory", "Fixed role directory is required", name))
        return False
    if entry[2] or not stat.S_ISDIR(entry[1].st_mode):
        issues.append(_issue("role_not_directory", "Fixed role path must be an ordinary directory", name))
        return False
    return True


def _check_outdated_directory(reference: Path, issues: list[Issue]) -> bool:
    relative = f"ref/{_OUTDATED}"
    path = reference / _OUTDATED
    try:
        value = path.lstat()
    except FileNotFoundError:
        issues.append(_issue("missing_outdated_directory", "Fixed outdated directory is required", relative))
        return False
    if _is_linklike(value, symlink=path.is_symlink()) or not stat.S_ISDIR(value.st_mode):
        issues.append(_issue("outdated_not_directory", "Fixed outdated path must be an ordinary directory", relative))
        return False
    return True


def _looks_like_knowledge_unit(directory: Path) -> bool:
    try:
        names = {entry.name for entry in os.scandir(directory)}
    except OSError:
        return False
    return bool(names & _GUIDES)


def _inspect_unit(path: Path):
    record = path / "record.json"
    private = ("record.json",) if record.is_file() and not record.is_symlink() else ()
    return inspect_envelope(logical_absolute(path), private)


def _check_control_path(relative: str, is_directory: bool, allowed: set[str], issues: list[Issue]) -> None:
    if relative in allowed:
        return
    folded = relative.rsplit("/", 1)[-1].casefold()
    if (is_directory and folded in _CONTROL_DIRECTORIES) or (not is_directory and folded in _CONTROL_FILES):
        issues.append(_issue("instruction_control_path", "Instruction-control paths are not workspace data", relative))


def _scan_safe_tree(
    directory: Path,
    issues: list[Issue],
    *,
    prefix: str = "",
    allowed_controls: set[str] | None = None,
    ku_aware: bool = False,
    excluded_paths: set[str] | None = None,
) -> None:
    allowed = allowed_controls or set()
    excluded = excluded_paths or set()
    entries = sorted(os.scandir(directory), key=lambda item: (item.name.casefold(), item.name))
    folded: dict[str, str] = {}
    for entry in entries:
        relative = f"{prefix}/{entry.name}" if prefix else entry.name
        key = _path_key(entry.name)
        if key in folded:
            issues.append(
                _issue("name_collision", "Sibling names collide under Unicode normalization and case folding", relative, names=[folded[key], entry.name])
            )
        else:
            folded[key] = entry.name
        message = _component_error(entry.name)
        if message is not None:
            issues.append(_issue("invalid_entry_name", message, relative))
        value = entry.stat(follow_symlinks=False)
        if _is_linklike(value, symlink=entry.is_symlink()):
            issues.append(_issue("link_not_allowed", "Links and reparse points are not valid workspace entries", relative))
            continue
        if relative in excluded:
            continue
        if stat.S_ISDIR(value.st_mode):
            _check_control_path(relative, True, allowed, issues)
            if ku_aware and _looks_like_knowledge_unit(Path(entry.path)):
                inspection = _inspect_unit(Path(entry.path))
                if inspection.valid:
                    continue
                issues.append(
                    _issue(
                        "invalid_knowledge_unit",
                        "Declared knowledge-unit directory does not satisfy Envelope v2",
                        relative,
                        issues=[item.as_dict() for item in inspection.issues],
                    )
                )
                continue
            _scan_safe_tree(
                Path(entry.path),
                issues,
                prefix=relative,
                allowed_controls=allowed,
                ku_aware=ku_aware,
                excluded_paths=excluded,
            )
        elif stat.S_ISREG(value.st_mode):
            _check_control_path(relative, False, allowed, issues)
        else:
            issues.append(_issue("non_regular_entry", "Workspace entries must be ordinary files or directories", relative))


def _outer_manifest(root: Path, issues: list[Issue]) -> tuple[str | None, dict[str, Any] | None]:
    relative = "collaborative-workspace.json"
    manifest = _load_manifest(root / relative, relative, issues)
    if manifest is None:
        return None, None
    _check_exact_keys(manifest, _OUTER_MANIFEST_KEYS, relative, issues)
    if manifest.get("contract") != COLLABORATIVE_WORKSPACE_CONTRACT:
        issues.append(_issue("manifest_contract_mismatch", "Manifest contract does not match the selected contract", relative))
    workspace_id = _canonical_uuid4(manifest.get("workspace_id"), f"{relative}#/workspace_id", issues)
    if manifest.get("roles") != _OUTER_ROLES:
        issues.append(_issue("invalid_role_paths", "Outer role paths must match the fixed contract", f"{relative}#/roles"))
    return workspace_id, manifest


def _validate_string_list(value: object, field: str, issues: list[Issue]) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        issues.append(_issue("invalid_manifest_schema", "Field must be an array of non-empty strings", field))
        return None
    return value


def _inner_manifest(
    root: Path, issues: list[Issue]
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, str]], list[dict[str, Any]]]:
    relative = "ref/.agent-workbench.json"
    manifest = _load_manifest(root / "ref" / ".agent-workbench.json", relative, issues)
    if manifest is None:
        return None, None, [], []
    _check_exact_keys(manifest, _INNER_MANIFEST_KEYS, relative, issues)
    if manifest.get("contract") != AGENT_WORKBENCH_CONTRACT:
        issues.append(_issue("manifest_contract_mismatch", "Manifest contract does not match the selected contract", relative))
    workspace_id = _canonical_uuid4(manifest.get("workspace_id"), f"{relative}#/workspace_id", issues)

    generation = manifest.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        issues.append(_issue("invalid_generation", "generation must be a positive integer", f"{relative}#/generation"))
    quality = manifest.get("quality")
    if quality not in _QUALITIES:
        issues.append(_issue("invalid_quality", "quality must be ready or ready_with_warnings", f"{relative}#/quality"))
    warnings = _validate_string_list(manifest.get("warnings"), f"{relative}#/warnings", issues)

    records_value = manifest.get("source_records")
    records: list[dict[str, str]] = []
    if not isinstance(records_value, list):
        issues.append(_issue("invalid_manifest_schema", "source_records must be an array", f"{relative}#/source_records"))
    else:
        for index, record in enumerate(records_value):
            field = f"{relative}#/source_records/{index}"
            if not isinstance(record, dict) or set(record) != _SOURCE_RECORD_KEYS:
                issues.append(_issue("invalid_source_record", "Source record fields do not match the contract", field))
                continue
            source_path = _validate_relative_path(record.get("path"), field + "/path", issues)
            kind = record.get("kind")
            digest = record.get("digest")
            if kind not in _SOURCE_KINDS:
                issues.append(_issue("invalid_source_kind", "Source kind is not supported", field + "/kind"))
            if not _valid_sha256(digest):
                issues.append(_issue("invalid_digest", "Digest must be lowercase SHA-256 hex", field + "/digest"))
            if source_path is not None and kind in _SOURCE_KINDS and _valid_sha256(digest):
                _validate_instruction_path(source_path, kind, field + "/path", issues)
                records.append({"path": source_path, "kind": kind, "digest": digest})
        paths = [record["path"] for record in records]
        if paths != sorted(paths):
            issues.append(_issue("noncanonical_order", "source_records must be sorted by path", f"{relative}#/source_records"))
        _validate_path_set(paths, f"{relative}#/source_records", issues)

    digest_value = manifest.get("source_tree_digest")
    if not _valid_sha256(digest_value):
        issues.append(_issue("invalid_digest", "source_tree_digest must be lowercase SHA-256 hex", f"{relative}#/source_tree_digest"))
    elif isinstance(records_value, list) and all(isinstance(item, dict) and set(item) == _SOURCE_RECORD_KEYS for item in records_value):
        expected = source_records_digest(records)
        if digest_value != expected:
            issues.append(_issue("source_tree_digest_mismatch", "source_tree_digest does not match canonical source_records", relative))

    items_value = manifest.get("items")
    items: list[dict[str, Any]] = []
    if not isinstance(items_value, list):
        issues.append(_issue("invalid_manifest_schema", "items must be an array", f"{relative}#/items"))
    else:
        for index, item in enumerate(items_value):
            field = f"{relative}#/items/{index}"
            if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
                issues.append(_issue("invalid_projection_item", "Projection item fields do not match the contract", field))
                continue
            source_path = _validate_relative_path(item.get("source_path"), field + "/source_path", issues)
            unit_path = _validate_relative_path(item.get("unit_path"), field + "/unit_path", issues)
            source_kind = item.get("source_kind")
            source_digest = item.get("source_digest")
            prepared_digest = item.get("prepared_digest")
            provider_route = item.get("provider_route")
            item_quality = item.get("quality")
            item_issues = _validate_string_list(item.get("issues"), field + "/issues", issues)
            if source_kind not in _SOURCE_KINDS:
                issues.append(_issue("invalid_source_kind", "Source kind is not supported", field + "/source_kind"))
            if not _valid_sha256(source_digest):
                issues.append(_issue("invalid_digest", "source_digest must be lowercase SHA-256 hex", field + "/source_digest"))
            if not _valid_sha256(prepared_digest):
                issues.append(_issue("invalid_digest", "prepared_digest must be lowercase SHA-256 hex", field + "/prepared_digest"))
            if provider_route not in _PROVIDER_ROUTES:
                issues.append(_issue("invalid_provider_route", "provider_route is not supported by the fixed contract", field + "/provider_route"))
            if item_quality not in _QUALITIES:
                issues.append(_issue("invalid_quality", "Item quality must be ready or ready_with_warnings", field + "/quality"))
            if source_path is not None and unit_path is not None and source_path != unit_path:
                issues.append(_issue("noncanonical_mapping", "unit_path must exactly equal source_path", field + "/unit_path"))
            if source_path is not None and source_kind in _SOURCE_KINDS:
                _validate_instruction_path(source_path, source_kind, field + "/source_path", issues)
            if unit_path is not None and source_kind in _SOURCE_KINDS:
                _validate_instruction_path(unit_path, source_kind, field + "/unit_path", issues)
            if item_quality == "ready" and item_issues:
                issues.append(_issue("quality_issue_mismatch", "ready items cannot contain issues", field))
            if item_quality == "ready_with_warnings" and item_issues == []:
                issues.append(_issue("quality_issue_mismatch", "ready_with_warnings items require an issue", field))
            if (
                source_path is not None
                and unit_path is not None
                and source_kind in _SOURCE_KINDS
                and _valid_sha256(source_digest)
                and _valid_sha256(prepared_digest)
                and provider_route in _PROVIDER_ROUTES
                and item_quality in _QUALITIES
                and item_issues is not None
            ):
                items.append(item)
        item_paths = [item["source_path"] for item in items]
        if item_paths != sorted(item_paths):
            issues.append(_issue("noncanonical_order", "items must be sorted by source_path", f"{relative}#/items"))
        _validate_path_set(item_paths, f"{relative}#/items", issues)

    record_by_path = {record["path"]: record for record in records}
    item_by_path = {item["source_path"]: item for item in items}
    if set(record_by_path) != set(item_by_path):
        issues.append(_issue("projection_inventory_mismatch", "source_records and items must map one-to-one", relative))
    for source_path in sorted(set(record_by_path) & set(item_by_path)):
        record = record_by_path[source_path]
        item = item_by_path[source_path]
        if item["source_kind"] != record["kind"] or item["source_digest"] != record["digest"]:
            issues.append(_issue("projection_source_mismatch", "Projection item source identity does not match its source record", source_path))
        expected_route = "knowledge-unit-copy" if record["kind"] == "knowledge_unit" else None
        if expected_route is not None and item["provider_route"] != expected_route:
            issues.append(_issue("invalid_provider_route", "Knowledge-unit sources must use knowledge-unit-copy", source_path))
        if record["kind"] == "knowledge_unit" and item["prepared_digest"] != record["digest"]:
            issues.append(_issue("knowledge_unit_copy_digest_mismatch", "Copied knowledge-unit bytes must retain the source tree digest", source_path))
        if record["kind"] == "file" and item["provider_route"] == "knowledge-unit-copy":
            issues.append(_issue("invalid_provider_route", "Ordinary files must use a conversion provider", source_path))

    has_warning = bool(warnings) or any(item.get("issues") for item in items)
    if quality == "ready" and has_warning:
        issues.append(_issue("quality_issue_mismatch", "ready manifests cannot contain warnings", relative))
    if quality == "ready_with_warnings" and not has_warning:
        issues.append(_issue("quality_issue_mismatch", "ready_with_warnings manifests require a warning", relative))
    return workspace_id, manifest, records, items


def _validate_projection(root: Path, items: list[dict[str, Any]], issues: list[Issue]) -> None:
    ref = root / "ref"
    if not ref.is_dir() or ref.is_symlink():
        return
    expected = {item["unit_path"]: item for item in items}
    containers: set[str] = set()
    for unit_path in expected:
        parts = unit_path.split("/")
        for index in range(1, len(parts)):
            containers.add("/".join(parts[:index]))

    found: set[str] = set()

    def visit(directory: Path, prefix: str) -> None:
        entries = sorted(os.scandir(directory), key=lambda entry: (entry.name.casefold(), entry.name))
        folded: dict[str, str] = {}
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            key = _path_key(entry.name)
            if key in folded:
                issues.append(_issue("name_collision", "Projection names collide under Unicode normalization and case folding", f"ref/{relative}"))
            else:
                folded[key] = entry.name
            message = _component_error(entry.name)
            if message is not None:
                issues.append(_issue("invalid_entry_name", message, f"ref/{relative}"))
            value = entry.stat(follow_symlinks=False)
            if _is_linklike(value, symlink=entry.is_symlink()):
                issues.append(_issue("link_not_allowed", "Links and reparse points are not valid projection entries", f"ref/{relative}"))
                continue
            if not prefix and entry.name == ".agent-workbench.json":
                if not stat.S_ISREG(value.st_mode):
                    issues.append(_issue("manifest_not_file", "Projection manifest must be an ordinary file", "ref/.agent-workbench.json"))
                continue
            if not prefix and entry.name == _OUTDATED:
                continue
            _check_control_path(f"ref/{relative}", stat.S_ISDIR(value.st_mode), set(), issues)
            if relative in expected:
                found.add(relative)
                if not stat.S_ISDIR(value.st_mode):
                    issues.append(_issue("unit_not_directory", "Projected knowledge unit must be a directory", f"ref/{relative}"))
                    continue
                unit_path = Path(entry.path)
                inspection = _inspect_unit(unit_path)
                if not inspection.valid:
                    issues.append(
                        _issue(
                            "invalid_prepared_unit",
                            "Projected item does not satisfy Knowledge Unit Envelope v2",
                            f"ref/{relative}",
                            issues=[item.as_dict() for item in inspection.issues],
                        )
                    )
                    continue
                actual_digest = canonical_tree_digest(unit_path)
                if actual_digest != expected[relative]["prepared_digest"]:
                    issues.append(_issue("prepared_digest_mismatch", "prepared_digest does not match projected knowledge-unit bytes", f"ref/{relative}"))
                continue
            if relative in containers:
                if stat.S_ISDIR(value.st_mode):
                    visit(Path(entry.path), relative)
                else:
                    issues.append(_issue("projection_container_not_directory", "Projection path prefix must be a directory", f"ref/{relative}"))
                continue
            issues.append(_issue("unexpected_projection_entry", "Prepared ref contains an undeclared entry", f"ref/{relative}"))

    visit(ref, "")
    for relative in sorted(set(expected) - found):
        issues.append(_issue("missing_prepared_unit", "Manifest-declared knowledge unit is missing", f"ref/{relative}"))


def _validate_outdated_projection(
    outdated: Path,
    current_generation: int | None,
    issues: list[Issue],
) -> None:
    generations: dict[int, str] = {}

    def visit_container(directory: Path, prefix: str) -> None:
        entries = sorted(os.scandir(directory), key=lambda item: (item.name.casefold(), item.name))
        if not entries:
            issues.append(_issue("empty_outdated_container", "Outdated batch containers must not be empty", prefix))
            return
        folded: dict[str, str] = {}
        for entry in entries:
            relative = f"{prefix}/{entry.name}"
            key = _path_key(entry.name)
            if key in folded:
                issues.append(
                    _issue(
                        "name_collision",
                        "Outdated names collide under Unicode normalization and case folding",
                        relative,
                        names=[folded[key], entry.name],
                    )
                )
            else:
                folded[key] = entry.name
            message = _component_error(entry.name)
            if message is not None:
                issues.append(_issue("invalid_entry_name", message, relative))
            value = entry.stat(follow_symlinks=False)
            if _is_linklike(value, symlink=entry.is_symlink()):
                issues.append(_issue("link_not_allowed", "Links and reparse points are not valid outdated entries", relative))
                continue
            _check_control_path(relative, stat.S_ISDIR(value.st_mode), set(), issues)
            if not stat.S_ISDIR(value.st_mode):
                issues.append(_issue("outdated_container_not_directory", "Outdated path containers must be directories", relative))
                continue
            unit = Path(entry.path)
            if _looks_like_knowledge_unit(unit):
                inspection = _inspect_unit(unit)
                if not inspection.valid:
                    issues.append(
                        _issue(
                            "invalid_outdated_knowledge_unit",
                            "Outdated item does not satisfy Knowledge Unit Envelope v2",
                            relative,
                            issues=[item.as_dict() for item in inspection.issues],
                        )
                    )
                continue
            visit_container(unit, relative)

    entries = sorted(os.scandir(outdated), key=lambda item: (item.name.casefold(), item.name))
    folded: dict[str, str] = {}
    for entry in entries:
        relative = f"ref/{_OUTDATED}/{entry.name}"
        key = _path_key(entry.name)
        if key in folded:
            issues.append(
                _issue(
                    "name_collision",
                    "Outdated batch names collide under Unicode normalization and case folding",
                    relative,
                    names=[folded[key], entry.name],
                )
            )
        else:
            folded[key] = entry.name
        message = _component_error(entry.name)
        if message is not None:
            issues.append(_issue("invalid_entry_name", message, relative))
        value = entry.stat(follow_symlinks=False)
        if _is_linklike(value, symlink=entry.is_symlink()):
            issues.append(_issue("link_not_allowed", "Links and reparse points are not valid outdated batches", relative))
            continue
        if not stat.S_ISDIR(value.st_mode):
            issues.append(_issue("outdated_batch_not_directory", "Outdated batches must be ordinary directories", relative))
            continue
        matched = _OUTDATED_BATCH.fullmatch(entry.name)
        if matched is None:
            issues.append(_issue("invalid_outdated_batch", "Outdated batch name does not match the fixed contract", relative))
        else:
            generation = int(matched.group(1))
            try:
                datetime.strptime(matched.group(2), "%Y%m%dT%H%MZ")
            except ValueError:
                issues.append(_issue("invalid_outdated_batch", "Outdated batch timestamp is not a real UTC minute", relative))
            previous = generations.get(generation)
            if previous is not None:
                issues.append(
                    _issue(
                        "duplicate_outdated_generation",
                        "At most one outdated batch is allowed for each retired generation",
                        relative,
                        batches=[previous, entry.name],
                    )
                )
            else:
                generations[generation] = entry.name
            if current_generation is not None and generation >= current_generation:
                issues.append(
                    _issue(
                        "invalid_outdated_generation",
                        "Outdated batch generation must be lower than the active manifest generation",
                        relative,
                        active_generation=current_generation,
                    )
                )
        visit_container(Path(entry.path), relative)


def inspect_workspace(path: Path, contract: str) -> WorkspaceInspection:
    logical_root, root, issues = _root_state(path, contract)
    if issues:
        return WorkspaceInspection(logical_root, contract, None, {}, _ordered(issues))
    entries = _entry_map(root, issues)
    _check_navigation(root, contract, entries, issues)

    if contract == COLLABORATIVE_WORKSPACE_CONTRACT:
        workspace_id, manifest = _outer_manifest(root, issues)
        ref_valid = _check_role_directory(entries, "ref", issues)
        _check_role_directory(entries, "agent-workbench", issues)
        outdated_valid = _check_outdated_directory(root / "ref", issues) if ref_valid else False
        for name, entry in entries.items():
            if name == "agent-workbench" or entry[2]:
                continue
            if name == "ref" and ref_valid:
                _scan_safe_tree(
                    entry[0],
                    issues,
                    prefix="ref",
                    ku_aware=True,
                    excluded_paths={f"ref/{_OUTDATED}"},
                )
            elif name not in {"AGENTS.md", "CLAUDE.md", "collaborative-workspace.json", "ref"} and stat.S_ISDIR(entry[1].st_mode):
                _check_control_path(name, True, set(), issues)
                _scan_safe_tree(entry[0], issues, prefix=name)
            elif name not in {"AGENTS.md", "CLAUDE.md", "collaborative-workspace.json", "ref"}:
                _check_control_path(name, False, set(), issues)
        if outdated_valid:
            _scan_safe_tree(root / "ref" / _OUTDATED, issues, prefix=f"ref/{_OUTDATED}", ku_aware=True)
        details = {
            "manifest_path": "collaborative-workspace.json",
            "roles": manifest.get("roles") if isinstance(manifest, dict) else None,
        }
    elif contract == AGENT_WORKBENCH_CONTRACT:
        ref_valid = _check_role_directory(entries, "ref", issues)
        _check_role_directory(entries, "temp", issues)
        _check_role_directory(entries, "output", issues)
        workspace_id, manifest, records, items = _inner_manifest(root, issues)
        outdated_valid = _check_outdated_directory(root / "ref", issues) if ref_valid else False
        for name, entry in entries.items():
            if name == "ref" or entry[2]:
                continue
            if name in {"AGENTS.md", "CLAUDE.md"}:
                continue
            if stat.S_ISDIR(entry[1].st_mode):
                _check_control_path(name, True, set(), issues)
                _scan_safe_tree(entry[0], issues, prefix=name)
            else:
                _check_control_path(name, False, set(), issues)
        if ref_valid:
            _validate_projection(root, items, issues)
        if outdated_valid:
            generation = manifest.get("generation") if isinstance(manifest, dict) else None
            current_generation = generation if isinstance(generation, int) and not isinstance(generation, bool) and generation > 0 else None
            _validate_outdated_projection(root / "ref" / _OUTDATED, current_generation, issues)
        details = {
            "manifest_path": "ref/.agent-workbench.json",
            "generation": manifest.get("generation") if isinstance(manifest, dict) else None,
            "quality": manifest.get("quality") if isinstance(manifest, dict) else None,
            "source_tree_digest": manifest.get("source_tree_digest") if isinstance(manifest, dict) else None,
            "source_records": records,
            "items": items,
            "warnings": manifest.get("warnings") if isinstance(manifest, dict) and isinstance(manifest.get("warnings"), list) else [],
        }
    else:
        raise RequestError(f"unknown workspace contract: {contract}")
    return WorkspaceInspection(logical_root, contract, workspace_id, details, _ordered(issues))


def validate_workspace(path: Path, contract: str) -> WorkspaceInspection:
    inspection = inspect_workspace(path, contract)
    if not inspection.valid:
        raise ValidationFailure(inspection)
    return inspection


def parse_workspace_request(request: dict[str, Any]) -> tuple[Path, str]:
    if set(request) != {"path", "contract"}:
        raise RequestError("request must contain exactly path and contract")
    raw_path = request["path"]
    if not isinstance(raw_path, str) or not raw_path:
        raise RequestError("path must be a non-empty absolute string")
    path = Path(raw_path)
    if not path.is_absolute():
        raise RequestError("path must be absolute")
    contract = request["contract"]
    if contract not in WORKSPACE_CONTRACTS:
        raise RequestError("contract must select a supported collaborative workspace contract")
    return logical_absolute(path), contract


def _write_missing(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()


def complete_workspace_stage(path: Path, contract: str) -> tuple[WorkspaceInspection, list[str]]:
    before = inspect_workspace(path, contract)
    role_names = ("ref", "agent-workbench") if contract == COLLABORATIVE_WORKSPACE_CONTRACT else ("ref", "temp", "output")
    repairable = {
        *(('missing_navigation_guide', name) for name in ("AGENTS.md", "CLAUDE.md")),
        *(('missing_role_directory', name) for name in role_names),
        ('missing_outdated_directory', f"ref/{_OUTDATED}"),
    }
    nonrepairable = [issue for issue in before.issues if (issue.code, issue.path) not in repairable]
    if nonrepairable:
        raise ValidationFailure(WorkspaceInspection(before.path, before.contract, before.workspace_id, before.details, _ordered(nonrepairable)))

    root = native_path(path)
    changes: list[str] = []
    agents, claude = workspace_navigation_bytes(contract)
    missing = {(issue.code, issue.path) for issue in before.issues}
    for name, payload in (("AGENTS.md", agents), ("CLAUDE.md", claude)):
        if ("missing_navigation_guide", name) in missing:
            _write_missing(root / name, payload)
            changes.append(name)
    for name in role_names:
        if ("missing_role_directory", name) in missing:
            (root / name).mkdir()
            changes.append(name + "/")
    outdated = root / "ref" / _OUTDATED
    if ("missing_role_directory", "ref") in missing or ("missing_outdated_directory", f"ref/{_OUTDATED}") in missing:
        outdated.mkdir()
        changes.append(f"ref/{_OUTDATED}/")

    after = inspect_workspace(path, contract)
    if not after.valid:
        raise ValidationFailure(after)
    return after, changes


def collaborative_workspace_capabilities() -> dict[str, Any]:
    common = {"path": "absolute directory path", "contract": list(WORKSPACE_CONTRACTS)}
    return {
        "version": VERSION,
        "contracts": list(WORKSPACE_CONTRACTS),
        "commands": list(COLLABORATIVE_WORKSPACE_COMMANDS),
        "requests": {
            "collaborative_workspace.capabilities": {},
            "collaborative_workspace.inspect": common,
            "collaborative_workspace.validate": common,
            "collaborative_workspace.stage.complete": common,
        },
        "fixed_paths": {
            COLLABORATIVE_WORKSPACE_CONTRACT: {
                "manifest": "collaborative-workspace.json",
                "roles": _OUTER_ROLES,
                "navigation": ["AGENTS.md", "CLAUDE.md"],
                "outdated": f"ref/{_OUTDATED}",
            },
            AGENT_WORKBENCH_CONTRACT: {
                "manifest": "ref/.agent-workbench.json",
                "roles": {"reference": "ref", "temporary": "temp", "output": "output"},
                "navigation": ["AGENTS.md", "CLAUDE.md"],
                "outdated": f"ref/{_OUTDATED}",
            },
        },
        "manifest_fields": {
            COLLABORATIVE_WORKSPACE_CONTRACT: sorted(_OUTER_MANIFEST_KEYS),
            AGENT_WORKBENCH_CONTRACT: sorted(_INNER_MANIFEST_KEYS),
            "source_record": sorted(_SOURCE_RECORD_KEYS),
            "projection_item": sorted(_ITEM_KEYS),
        },
        "manifest_values": {
            "source_kinds": sorted(_SOURCE_KINDS),
            "provider_routes": sorted(_PROVIDER_ROUTES),
            "qualities": sorted(_QUALITIES),
        },
        "instruction_control": {
            "files_case_insensitive": sorted(_CONTROL_FILES),
            "directories_case_insensitive": sorted(_CONTROL_DIRECTORIES),
            "fixed_navigation_exceptions": ["AGENTS.md", "CLAUDE.md"],
        },
        "canonicalization": {
            "relative_paths": "NFC relative POSIX paths with portable components",
            "collision_key": "Unicode NFC then casefold",
            "sha256": "lowercase 64-character hex",
            "canonical_json": "UTF-8, ensure_ascii=false, sorted object keys, separators comma and colon",
            "tree_records": {
                "directory": ["kind", "path"],
                "file": ["digest", "kind", "path"],
                "includes_root": False,
            },
        },
        "outdated": {
            "outer": "safe user-managed ordinary hierarchy excluded from active sources",
            "inner_batch": "generation-<old-generation>-<UTC YYYYMMDDTHHMMZ>",
            "inner_items": "Knowledge Unit Envelope v2 roots at original source-relative paths",
        },
        "mutation_boundary": {
            "stage_only": True,
            "adds_missing_navigation_and_fixed_directories_only": True,
            "creates_manifests_or_payloads": False,
            "moves_or_publishes_roots": False,
            "conversion_or_sync": False,
            "cleanup_or_recovery": False,
        },
    }


__all__ = [
    "canonical_tree_digest",
    "collaborative_workspace_capabilities",
    "complete_workspace_stage",
    "inspect_workspace",
    "parse_workspace_request",
    "source_records_digest",
    "validate_workspace",
]
