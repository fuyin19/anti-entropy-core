# anti-entropy-core

`anti-entropy-core` is a small, standard-library-only authority for Knowledge Unit Envelope v2 and the Collaborative Workspace / Agent Workbench v1 directory contracts. It inspects and validates contract directories and can complete only a caller-provided disposable stage. It never converts content, synchronizes workspaces, or moves, renames, or publishes a root.

Run the JSON Lines interface with an explicit interpreter and script path:

```text
ABS_PYTHON_3_11 -I -S ABS_REPO/scripts/knowledge_unit_runner.py
```

Each stdin line is an object with exactly `command` and `request`:

```json
{"command":"validate","request":{"path":"C:\\absolute\\unit","private_root_files":[]}}
```

Each input receives one Result object containing the six semantic fields `abi`, `status`, `exit_code`, `command`, `data`, and `issues`. JSON member order is not part of the ABI. See [docs/CONTRACT.md](docs/CONTRACT.md).

Collaborative Workspace commands use the same runner and explicitly select one contract:

```json
{"command":"collaborative_workspace.validate","request":{"path":"C:\\absolute\\workspace","contract":"collaborative-workspace-envelope/v1"}}
```

The legacy `capabilities.data.commands` list remains unchanged. Discover the workspace extension through `capabilities.data.extensions.collaborative_workspace`, then call `collaborative_workspace.capabilities` for its schemas and mutation boundary.

Both workspace contracts require `ref/_outdated/`. The outer role is a safe human-owned archive excluded from active projection; the inner role accepts only generation-named batches whose leaves are valid Knowledge Unit Envelope v2 roots. Workspace stage completion may create the missing empty role but never archives or moves payload itself.

Run the tests with:

```text
python -m unittest discover -s tests -v
```
