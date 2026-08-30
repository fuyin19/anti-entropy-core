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

`private_root_files` is optional and defaults to `[]`.

Every response is a JSON object containing exactly the semantic fields `abi`, `status`, `exit_code`, `command`, `data`, and `issues`. Their serialized member order is not an ABI. The ABI is `anti-entropy-core.runner/v1`; status/code pairs are `ok/0`, `usage_error/2`, `validation_error/3`, and `io_error/6`. A line's `exit_code` is result data and does not terminate the persistent process. EOF exits normally.

## Envelope v2

A complete unit is one ordinary directory containing:

- byte-exact `AGENTS.md` and `CLAUDE.md` navigation files;
- at least one ordinary root representation file;
- root representations with one exact common stem, extensions, and case-fold-distinct names/extensions;
- optional exact `record.json` only when declared by `private_root_files`;
- `assets/` and `src/` directories.

An empty support directory contains only a zero-byte `.keep`. A non-empty `src/` contains exactly one direct ordinary source file. `assets/` may be recursive, but nested empty directories are invalid. Symlinks and non-regular filesystem objects are invalid. Instruction-control filenames or directories are not data and are rejected except for the two exact root navigation files.

## Mutation boundary

The caller is responsible for providing an owned, disposable stage. `repair` may only add a missing exact navigation file, create a missing `assets/` or `src/`, and add a zero-byte `.keep` to a missing or physically empty support directory. It refuses nonmatching existing guides and all other structural defects before writing. It never changes or removes representations, source files, assets, or private files.

`stage.complete` performs the same fixed local completion and then strict validation. Neither mutation command moves, renames, or publishes the root. There is no lock, journal, receipt, rollback, recovery, cleanup route, network access, or trusted-launch subsystem.

