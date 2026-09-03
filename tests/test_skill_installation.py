from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).absolute().parents[1]
SKILL_ROOT = ROOT / "skills" / "anti-entropy-core"
ABI = "anti-entropy-core.runner/v1"
VERSION = "1.2.1"
OUTER = "collaborative-workspace-envelope/v1"
INNER = "agent-workbench-envelope/v1"
WORKSPACE_ID = "915aedc2-46ac-4aaf-98fa-8e0d3d853aae"
RESULT_FIELDS = {"abi", "status", "exit_code", "command", "data", "issues"}
# Byte identities from the 1.2.0 contract resources, independent of the installed copy.
RESOURCE_SHA256 = {
    "AGENTS.md": "2067837a839ba3a9a452504a1f85bcff738eb7a181a77458105a8096a33f1bcc",
    "CLAUDE.md": "336cc4fbf19beaada7ccf9986414fa91851a8d7a07dfb3ccbe800a69eed0ab49",
    "COLLABORATIVE_WORKSPACE_AGENTS.md": "9a435b118dfd6ad62db4992f64687f8caefc073cbde65ab23b05332b8f224e24",
    "AGENT_WORKBENCH_AGENTS.md": "8f247d6828b67419be6025c1772d3be9cdba9d1498d36b6ddcdc4b0cb365efbc",
}
MODULE_NAMES = {
    "__init__.py", "constants.py", "envelope.py", "model.py", "operations.py",
    "paths.py", "protocol.py", "result.py", "workspace.py",
}


def _snapshot(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _tree_digest(root: Path) -> str:
    entries = []
    for relative, payload in sorted(_snapshot(root).items()):
        entry = {"kind": "directory" if payload is None else "file", "path": relative}
        if payload is not None:
            entry["digest"] = hashlib.sha256(payload).hexdigest()
        entries.append(entry)
    return hashlib.sha256(_canonical(entries)).hexdigest()


def _outer_manifest() -> dict[str, object]:
    return {
        "contract": OUTER,
        "workspace_id": WORKSPACE_ID,
        "roles": {"reference": "ref", "agent_workbench": "agent-workbench"},
    }


def _inner_manifest(unit: Path | None = None) -> dict[str, object]:
    records = []
    items = []
    if unit is not None:
        digest = _tree_digest(unit)
        records.append({"path": "reference-unit", "kind": "knowledge_unit", "digest": digest})
        items.append({
            "source_path": "reference-unit", "source_kind": "knowledge_unit",
            "source_digest": digest, "unit_path": "reference-unit", "prepared_digest": digest,
            "provider_route": "knowledge-unit-copy", "quality": "ready", "issues": [],
        })
    return {
        "contract": INNER, "workspace_id": WORKSPACE_ID, "generation": 1, "quality": "ready",
        "source_records": records, "source_tree_digest": hashlib.sha256(_canonical(records)).hexdigest(),
        "items": items, "warnings": [],
    }


class InstalledSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="core-skill-install-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        original_root = self.base / "original installation"
        original_skill = original_root / "skills" / "anti-entropy-core"
        shutil.copytree(SKILL_ROOT, original_skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.installation = self.base / "搬迁后的 install root"
        original_root.rename(self.installation)
        self.assertFalse(original_root.exists())
        self.skill = self.installation / "skills" / "anti-entropy-core"
        self.runner = self.skill / "scripts" / "knowledge_unit_runner.py"
        self.original_installation_bytes = _snapshot(self.skill)
        self.cwd = self.base / "unrelated cwd"
        self.cwd.mkdir()
        # Both normal script-directory lookup and PYTHONPATH would load this poison package.
        poison = self.cwd / "anti_entropy_core"
        poison.mkdir()
        (poison / "__init__.py").write_text("raise AssertionError('noninstalled Core imported')\n", encoding="utf-8")
        empty_path = self.base / "empty PATH"
        empty_path.mkdir()
        self.environment = dict(os.environ, PATH=str(empty_path), PYTHONPATH=str(self.cwd))

    def _run(self, payload: bytes, *, runner: Path | None = None, args: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", "-S", str(runner or self.runner), *args],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.cwd, env=self.environment, timeout=30, check=False,
        )

    def _call(self, command: str, request: dict[str, object], *, status: str = "ok") -> dict[str, object]:
        completed = self._run(_canonical({"command": command, "request": request}) + b"\n")
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stderr, b"")
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        result = json.loads(lines[0])
        self.assertEqual(set(result), RESULT_FIELDS)
        self.assertEqual((result["abi"], result["command"], result["status"]), (ABI, command, status), result)
        self.assertEqual(result["exit_code"], 0 if status == "ok" else 3, result)
        return result

    def _assert_preserved(self, root: Path, before: dict[str, bytes | None], additions: set[str]) -> None:
        after = _snapshot(root)
        self.assertEqual(set(after) - set(before), additions)
        self.assertEqual({path: after[path] for path in before}, before)

    def _complete_and_validate(self, root: Path, request: dict[str, object], additions: set[str], *, workspace: bool = False) -> None:
        before = _snapshot(root)
        prefix = "collaborative_workspace." if workspace else ""
        completed = self._call(prefix + "stage.complete", request)
        self.assertTrue(completed["data"]["completed"])
        self._assert_preserved(root, before, additions)
        after = _snapshot(root)
        for command in ("inspect", "validate"):
            self.assertTrue(self._call(prefix + command, request)["data"]["valid"])
            self.assertEqual(_snapshot(root), after)
        self.assertEqual(self._call(prefix + "stage.complete", request)["data"]["changes"], [])
        self.assertEqual(_snapshot(root), after)

    def test_relocated_skill_is_complete_and_resources_match_baseline(self) -> None:
        package = self.skill / "src" / "anti_entropy_core"
        self.assertEqual({path.name for path in package.glob("*.py")}, MODULE_NAMES)
        self.assertEqual({path.name for path in (package / "resources").iterdir()}, set(RESOURCE_SHA256))
        for name, expected in RESOURCE_SHA256.items():
            self.assertEqual(hashlib.sha256((package / "resources" / name).read_bytes()).hexdigest(), expected, name)
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertTrue((self.skill / "references" / "CONTRACT.md").is_file())
        self.assertTrue(self.runner.is_file())
        self.assertFalse((ROOT / "src" / "anti_entropy_core").exists())
        source_bytes = _snapshot(SKILL_ROOT)
        source_bytes = {path: value for path, value in source_bytes.items() if "__pycache__" not in Path(path).parts}
        self.assertEqual(self.original_installation_bytes, source_bytes)
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], VERSION)
        self.assertEqual(project["project"]["dependencies"], [])
        self.assertEqual(project["tool"]["setuptools"]["package-dir"][""], "skills/anti-entropy-core/src")
        self.assertEqual(project["tool"]["setuptools"]["package-data"]["anti_entropy_core"], ["resources/*.md"])

    def test_relocated_skill_queries_and_completes_all_contracts_without_source_or_global_imports(self) -> None:
        probe = subprocess.run(
            [sys.executable, "-I", "-S", "-c", "import importlib.util; assert importlib.util.find_spec('anti_entropy_core') is None"],
            cwd=self.cwd, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr.decode(errors="replace"))
        for command in ("capabilities", "collaborative_workspace.capabilities"):
            self.assertEqual(self._call(command, {})["data"]["version"], VERSION)

        unit = self.base / "disposable KU stage"
        unit.mkdir()
        (unit / "memo.md").write_bytes("# 保留的内容\n".encode("utf-8"))
        (unit / "memo.pdf").write_bytes(b"synthetic representation")
        (unit / "record.json").write_bytes(b'{"owned":"by caller"}\n')
        (unit / "src").mkdir()
        (unit / "src" / "memo.docx").write_bytes(b"synthetic original")
        (unit / "assets").mkdir()
        (unit / "assets" / "figure.bin").write_bytes(b"synthetic asset")
        self._complete_and_validate(
            unit, {"path": str(unit), "private_root_files": ["record.json"]}, {"AGENTS.md", "CLAUDE.md"},
        )

        outer = self.base / "disposable outer stage"
        outer.mkdir()
        (outer / "collaborative-workspace.json").write_bytes(_canonical(_outer_manifest()))
        (outer / "human-data.txt").write_bytes(b"caller-owned reference")
        self._complete_and_validate(
            outer, {"path": str(outer), "contract": OUTER},
            {"AGENTS.md", "CLAUDE.md", "ref", "ref/_outdated", "agent-workbench"}, workspace=True,
        )

        inner = outer / "agent-workbench"
        projected = inner / "ref" / "reference-unit"
        shutil.copytree(unit, projected)
        (inner / "ref" / ".agent-workbench.json").write_bytes(_canonical(_inner_manifest(projected)))
        (inner / "output").mkdir()
        (inner / "output" / "draft.txt").write_bytes(b"existing business output")
        self._complete_and_validate(
            inner, {"path": str(inner), "contract": INNER},
            {"AGENTS.md", "CLAUDE.md", "ref/_outdated", "temp"}, workspace=True,
        )
        outer_result = self._call("collaborative_workspace.validate", {"path": str(outer), "contract": OUTER})
        inner_result = self._call("collaborative_workspace.validate", {"path": str(inner), "contract": INNER})
        self.assertEqual(outer_result["data"]["workspace_id"], inner_result["data"]["workspace_id"])
        resources = self.skill / "src" / "anti_entropy_core" / "resources"
        for root, name in ((unit, "AGENTS.md"), (outer, "COLLABORATIVE_WORKSPACE_AGENTS.md"), (inner, "AGENT_WORKBENCH_AGENTS.md")):
            self.assertEqual((root / "AGENTS.md").read_bytes(), (resources / name).read_bytes())
            self.assertEqual((root / "CLAUDE.md").read_bytes(), (resources / "CLAUDE.md").read_bytes())
        self.assertEqual(_snapshot(self.skill), self.original_installation_bytes)

    def test_relocated_skill_refuses_nonrepairable_stages_without_any_write(self) -> None:
        for label, contract in (("KU", None), ("outer", OUTER), ("inner", INNER)):
            with self.subTest(contract=label):
                root = self.base / (label + " invalid stage")
                root.mkdir()
                request: dict[str, object] = {"path": str(root)}
                prefix = ""
                if contract == OUTER:
                    (root / "collaborative-workspace.json").write_bytes(_canonical(_outer_manifest()))
                elif contract == INNER:
                    (root / "ref").mkdir()
                    (root / "ref" / ".agent-workbench.json").write_bytes(_canonical(_inner_manifest()))
                else:
                    (root / "memo.md").write_bytes(b"unchanged payload")
                if contract:
                    request["contract"] = contract
                    prefix = "collaborative_workspace."
                (root / "AGENTS.md").write_bytes(b"nonmatching caller file")
                before = _snapshot(root)
                self.assertFalse(self._call(prefix + "inspect", request)["data"]["valid"])
                self.assertEqual(_snapshot(root), before)
                for command in ("validate", "stage.complete"):
                    self._call(prefix + command, request, status="validation_error")
                    self.assertEqual(_snapshot(root), before)
        self.assertEqual(_snapshot(self.skill), self.original_installation_bytes)

    def test_legacy_source_runner_forwards_the_same_jsonl_and_argv_behavior(self) -> None:
        legacy = self.installation / "scripts" / "knowledge_unit_runner.py"
        legacy.parent.mkdir()
        shutil.copy2(ROOT / "scripts" / "knowledge_unit_runner.py", legacy)
        payload = b'not json\n{"command":"capabilities","request":{}}\n{"command":"collaborative_workspace.capabilities","request":{}}\n'
        canonical = self._run(payload)
        forwarded = self._run(payload, runner=legacy)
        self.assertEqual(canonical.returncode, 0)
        self.assertEqual((forwarded.returncode, forwarded.stdout, forwarded.stderr), (canonical.returncode, canonical.stdout, canonical.stderr))
        canonical_argv = self._run(b"", args=("validate",))
        forwarded_argv = self._run(b"", runner=legacy, args=("validate",))
        self.assertEqual(canonical_argv.returncode, 2)
        self.assertEqual((forwarded_argv.returncode, forwarded_argv.stdout, forwarded_argv.stderr), (canonical_argv.returncode, canonical_argv.stdout, canonical_argv.stderr))


if __name__ == "__main__":
    unittest.main()
