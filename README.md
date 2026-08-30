# anti-entropy-core

`anti-entropy-core` is a small, standard-library-only implementation of the Knowledge Unit Envelope v2 contract. It inspects and validates knowledge-unit directories and can complete only a caller-provided disposable stage. It never moves, renames, or publishes a root.

Run the JSON Lines interface with an explicit interpreter and script path:

```text
ABS_PYTHON_3_11 -I -S ABS_REPO/scripts/knowledge_unit_runner.py
```

Each stdin line is an object with exactly `command` and `request`:

```json
{"command":"validate","request":{"path":"C:\\absolute\\unit","private_root_files":[]}}
```

Each input receives one Result object containing the six semantic fields `abi`, `status`, `exit_code`, `command`, `data`, and `issues`. JSON member order is not part of the ABI. See [docs/CONTRACT.md](docs/CONTRACT.md).

Run the tests with:

```text
python -m unittest discover -s tests -v
```

