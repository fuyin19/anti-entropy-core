# Minimal Core contract

## JSONL runner

Invoke `scripts/knowledge_unit_runner.py` with Python 3.11 or newer. It accepts no command-line route. Each UTF-8, LF-delimited stdin value must be exactly:

```json
{"command":"capabilities","request":{}}
```

The supported commands are:

- `capabilities`: `{}`
- `inspect`: `{"path": ABS, "private_root_files": [] | ["record.json"]}`
- `validate`: the same request
- `repair`: the same request
- `stage.complete`: the same request

The separately discoverable Collaborative Workspace extension supports exactly:

- `collaborative_workspace.capabilities`: `{}`
- `collaborative_workspace.inspect`: `{"path": ABS, "contract": CONTRACT}`
- `collaborative_workspace.validate`: the same request
- `collaborative_workspace.stage.complete`: the same request

`CONTRACT` must be exactly `collaborative-workspace-envelope/v1` or `agent-workbench-envelope/v1`. The legacy `capabilities.data.commands` value remains the original five-item list. Its separate `extensions` member points to `collaborative_workspace.capabilities`.

`private_root_files` is optional and defaults to `[]`.

Every response is a JSON object containing exactly the semantic fields `abi`, `status`, `exit_code`, `command`, `data`, and `issues`. Their serialized member order is not an ABI. The ABI is `anti-entropy-core.runner/v1`; status/code pairs are `ok/0`, `usage_error/2`, `validation_error/3`, and `io_error/6`. A line's `exit_code` is result data and does not terminate the persistent process. EOF exits normally.

## Envelope v2

A complete unit is one ordinary directory containing:

- byte-exact `AGENTS.md` and `CLAUDE.md` navigation files;
- at least one ordinary root representation file;
- root representations with one exact common stem, extensions, and case-fold-distinct names/extensions;
- optional exact `record.json` only when declared by `private_root_files`;
- `assets/` and `src/` directories.

An empty support directory contains only a zero-byte `.keep`. A non-empty `src/` contains exactly one direct ordinary source file. `assets/` may be recursive, but nested empty directories are invalid. Symlinks, junctions/reparse points, and non-regular filesystem objects are invalid. Instruction-control filenames or directories are not data and are rejected except for the two exact root navigation files.

## Mutation boundary

The caller is responsible for providing an owned, disposable stage. `repair` may only add a missing exact navigation file, create a missing `assets/` or `src/`, and add a zero-byte `.keep` to a missing or physically empty support directory. It refuses nonmatching existing guides and all other structural defects before writing. It never changes or removes representations, source files, assets, or private files.

`stage.complete` performs the same fixed local completion and then strict validation. Neither mutation command moves, renames, or publishes the root. There is no lock, journal, receipt, rollback, recovery, cleanup route, network access, or trusted-launch subsystem.

## Collaborative Workspace envelope v1

The outer contract root contains byte-exact contract `AGENTS.md` and `CLAUDE.md`, `collaborative-workspace.json`, and ordinary `ref/` and `agent-workbench/` directories. `ref/_outdated/` is a required human-owned archive role excluded from active projection; its safe ordinary hierarchy may contain valid Knowledge Unit Envelope v2 roots. Safe additional ordinary entries are allowed and have no Core-defined lifecycle. The outer validator treats `agent-workbench/` as the independently validated inner contract root.

The outer manifest has exactly:

```json
{
  "contract": "collaborative-workspace-envelope/v1",
  "workspace_id": "CANONICAL-LOWERCASE-UUIDV4",
  "roles": {
    "reference": "ref",
    "agent_workbench": "agent-workbench"
  }
}
```

The outer `stage.complete` requires this manifest to exist and be valid. It may exclusively add only missing fixed guides, `ref/`, `ref/_outdated/`, and `agent-workbench/`. It does not construct or validate the inner contract.

## Agent Workbench envelope v1

The inner contract root contains byte-exact contract `AGENTS.md` and `CLAUDE.md` plus ordinary `ref/`, `temp/`, and `output/` directories. Safe additional ordinary entries are allowed and have no Core-defined lifecycle. Active `ref/` is strict: it contains `.agent-workbench.json`, optional path-container directories, exactly the manifest-declared Knowledge Unit Envelope v2 roots, and the required system-owned `_outdated/` history. `temp/` and `output/` may contain safe ordinary data.

The inner manifest at `ref/.agent-workbench.json` has exactly:

```json
{
  "contract": "agent-workbench-envelope/v1",
  "workspace_id": "CANONICAL-LOWERCASE-UUIDV4",
  "generation": 1,
  "quality": "ready",
  "source_records": [
    {"path": "a/report.docx", "kind": "file", "digest": "LOWERCASE-SHA256-HEX"}
  ],
  "source_tree_digest": "LOWERCASE-SHA256-HEX",
  "items": [
    {
      "source_path": "a/report.docx",
      "source_kind": "file",
      "source_digest": "LOWERCASE-SHA256-HEX",
      "unit_path": "a/report.docx",
      "prepared_digest": "LOWERCASE-SHA256-HEX",
      "provider_route": "file-conversion",
      "quality": "ready",
      "issues": []
    }
  ],
  "warnings": []
}
```

`generation` is a positive integer. Quality is `ready` or `ready_with_warnings`; warning quality requires a non-empty manifest warning or item issue, while ready quality permits neither. Source kinds are `file` and `knowledge_unit`. Fixed provider routes are `file-conversion`, `markdown-conversion`, and `knowledge-unit-copy`; copied Knowledge Units must use the last route and retain the same source/prepared tree digest. Records and items are sorted and one-to-one, and `unit_path` exactly equals `source_path` so a full source basename is preserved. The manifest remains active-only: `_outdated/` is not listed in records or items.

Inner history batches are named exactly `generation-<old-generation>-<UTC YYYYMMDDTHHMMZ>`. At most one batch may exist per retired generation, its generation must be lower than the active manifest generation, it must be nonempty, and its leaves at original source paths must be valid Knowledge Unit Envelope v2 roots. Intermediate archive entries are directories only.

The inner `stage.complete` requires `ref/.agent-workbench.json` and its declared Knowledge Units to exist and validate first. It may exclusively add only missing fixed guides, `ref/_outdated/`, `temp/`, and `output/`. It never creates a manifest or payload.

## Workspace canonicalization and safety

Manifest paths are non-empty, relative POSIX paths in Unicode NFC. Absolute paths, backslashes, empty, dot, or dot-dot components, ASCII controls, Windows-invalid characters, trailing dots/spaces, and Windows reserved device basenames are invalid. Paths must be distinct after NFC plus case folding and cannot have a file/directory-prefix relationship.

Canonical JSON is UTF-8 from `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`. `source_tree_digest` is the lowercase SHA-256 hex digest of canonical JSON for the already path-sorted `source_records` array.

A prepared Knowledge Unit digest uses a path-sorted array containing every descendant of the unit root (the root itself is excluded):

- directory: `{"kind":"directory","path":REL}`;
- ordinary file: `{"digest":FILE_SHA256_HEX,"kind":"file","path":REL}`.

`prepared_digest` is the lowercase SHA-256 hex digest of that array's canonical JSON. Times, modes, and absolute paths are excluded.

Symlinks, junctions/reparse points, and non-regular entries are invalid. Instruction-control names are rejected case-insensitively: files `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`, `CLAUDE.local.md`, `.cursorrules`, and `.mcp.json`; directories `.claude` and `.cursor`. Exceptions are the two exact contract-root guides and the exact root guides inside a strictly valid Knowledge Unit Envelope v2.

Outer and inner manifests carry the same workspace ID by orchestration contract. Because their validators are deliberately independent and take different roots, a caller validating a complete workspace must validate both roots and compare the two returned `workspace_id` values.

## Workspace mutation boundary

Workspace inspection and validation are read-only. Workspace stage completion operates only in the supplied existing directory after refusing every non-repairable defect. It exclusively creates missing fixed navigation files or role directories, then strictly validates the selected layer. It never creates or changes manifests, payloads, reference data, prepared units, temporary work, or outputs. It does not convert, synchronize, publish, lock, clean, roll back, or recover anything.
