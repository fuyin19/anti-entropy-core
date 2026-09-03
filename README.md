# anti-entropy-core

`anti-entropy-core` is a small, standard-library-only authority for Knowledge Unit Envelope v2 and the Collaborative Workspace / Agent Workbench v1 directory contracts. It inspects and validates contract directories and can complete only a caller-provided disposable stage. It never converts content, synchronizes workspaces, or moves, renames, or publishes a root.

The 1.2.1 release is distributed as the complete, relocatable skill directory [skills/anti-entropy-core](skills/anti-entropy-core/SKILL.md). Copy that entire directory into the chosen skills installation root with its exact `anti-entropy-core` name. It contains the runner, all Python implementation modules, fixed navigation resources, and [contract reference](skills/anti-entropy-core/references/CONTRACT.md). No source checkout, global Python package, or third-party runtime dependency is required.

Run the JSON Lines interface with an explicit absolute Python 3.11-or-newer interpreter and installed script path:

```text
ABS_PYTHON_3_11 -I -S ABS_SKILLS_ROOT/anti-entropy-core/scripts/knowledge_unit_runner.py
```

Each stdin line is an object with exactly `command` and `request`:

```json
{"command":"validate","request":{"path":"C:\\absolute\\unit","private_root_files":[]}}
```

Each input receives one Result object containing the six semantic fields `abi`, `status`, `exit_code`, `command`, `data`, and `issues`. JSON member order is not part of the ABI. The ABI remains `anti-entropy-core.runner/v1`. Query `capabilities` or `collaborative_workspace.capabilities` and read the actual `data.version` for the installed version.

Collaborative Workspace commands use the same runner and explicitly select one contract:

```json
{"command":"collaborative_workspace.validate","request":{"path":"C:\\absolute\\workspace","contract":"collaborative-workspace-envelope/v1"}}
```

The legacy `capabilities.data.commands` list remains unchanged. Discover the workspace extension through `capabilities.data.extensions.collaborative_workspace`, then call `collaborative_workspace.capabilities` for its schemas and mutation boundary.

Both workspace contracts require `ref/_outdated/`. The outer role is a safe human-owned archive excluded from active projection; the inner role accepts only generation-named batches whose leaves are valid Knowledge Unit Envelope v2 roots. Workspace stage completion may create the missing empty role but never archives or moves payload itself.

The skill is a query and contract reference entrypoint. Business consumers continue to own preparation, conversion, publication, and root lifecycle operations. Matched consumers use their own installation root to locate the single sibling `anti-entropy-core/scripts/knowledge_unit_runner.py`, or accept the explicit absolute `ANTI_ENTROPY_CORE_RUNNER` override. They require the exact pinned Core version and ABI before business writes, and access Core only through isolated JSONL subprocesses.

The repository keeps one maintained implementation under `skills/anti-entropy-core/src`. The root `pyproject.toml` packages that same source and its resources; the legacy repository `scripts/knowledge_unit_runner.py` only forwards to the fixed skill runner path. It performs no discovery or fallback. Installing the complete skill does not require building or installing the optional Python package.

This release closes Core distribution, binding, and version checks. It does not fix the conversion skills' remaining `_shared` or sibling conversion runtime dependencies, and it does not claim a successful independently installed full conversion or AC26 prepare.

Run the tests with:

```text
python -m unittest discover -s tests -v
```

The suite includes an actual copied installation moved to a new path containing spaces and Chinese characters. Its isolated `-I -S` processes query both capabilities and complete/validate Knowledge Unit, outer workspace, and inner workbench stages, compare fixed resource bytes with the 1.2.0 baseline, and check payload preservation and read-only operations.
