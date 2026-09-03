---
name: anti-entropy-core
description: Query the installed anti-entropy Core version, ABI, capabilities, runner location, and Knowledge Unit or Collaborative Workspace contracts. Use for Core contract and integration questions.
---

# anti-entropy Core

This skill carries the complete standard-library Core implementation and its fixed contract resources. Core is the authority for Knowledge Unit Envelope v2, Collaborative Workspace Envelope v1, and Agent Workbench Envelope v1. Read [references/CONTRACT.md](references/CONTRACT.md) when explaining schemas, validation, or mutation boundaries.

## Locate and query this installation

Derive the absolute skill directory from this `SKILL.md` location. Its runner is exactly `scripts/knowledge_unit_runner.py` under that directory. Report that actual absolute path when the user asks where Core is installed; do not assume a source checkout, current working directory, or a user-wide skills root.

Use an explicitly identified absolute Python 3.11-or-newer interpreter path to invoke the runner with `-I -S`. The runner accepts UTF-8 JSON Lines on stdin and no command-line route. Send either or both read-only queries, each ending with LF:

```json
{"command":"capabilities","request":{}}
{"command":"collaborative_workspace.capabilities","request":{}}
```

Report the `abi` and `data.version` from the actual successful Result, plus the requested capabilities. This release is 1.2.1 with ABI `anti-entropy-core.runner/v1`; the live response is authoritative for the installation being queried. If the interpreter or runner cannot be invoked, report the concrete missing prerequisite instead of claiming a verified version. There is no `doctor` or installation command.

## Consumer integration and scope

Install the whole `anti-entropy-core` directory, preserving its `scripts/`, `src/`, and `references/` contents. A matched consumer normally selects the sibling `anti-entropy-core/scripts/knowledge_unit_runner.py` within its own determined skills installation root. `ANTI_ENTROPY_CORE_RUNNER` is the consumer's explicit absolute-path override for a different root. Consumers validate the exact ABI and their pinned Core version before business writes, and invoke Core through an isolated JSONL subprocess. They must not import this implementation into their own process.

The skill entrypoint is for contract explanation, location, and the two capability queries. Do not use it to execute `repair`, `stage.complete`, prepare, conversion, publication, or root lifecycle operations. Existing business consumers own those operations and may ask Core only to complete their disposable stages within the contract's fixed mutation boundary.

This carrier closes Core distribution and binding only. It does not make the conversion skills' other `_shared` or sibling conversion runtime dependencies independently installable, and does not establish that a complete business conversion or AC26 prepare succeeds.
