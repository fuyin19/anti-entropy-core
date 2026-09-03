from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
SKILL_ROOT = ROOT / "skills" / "anti-entropy-core"
sys.path.insert(0, str(SKILL_ROOT / "src"))

from anti_entropy_core.constants import (  # noqa: E402
    ABI,
    AGENT_WORKBENCH_CONTRACT,
    COLLABORATIVE_WORKSPACE_CONTRACT,
    VERSION,
    navigation_bytes,
    workspace_navigation_bytes,
)
from anti_entropy_core.protocol import dispatch  # noqa: E402
from anti_entropy_core.paths import native_path  # noqa: E402
from anti_entropy_core.workspace import canonical_tree_digest, source_records_digest  # noqa: E402

RUNNER = SKILL_ROOT / "scripts" / "knowledge_unit_runner.py"
RESULT_FIELDS = {"abi", "status", "exit_code", "command", "data", "issues"}
HEX_A = "a" * 64


def _request(path: Path, contract: str) -> dict[str, str]:
    return {"path": str(path), "contract": contract}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _outer_manifest(workspace_id: str) -> dict[str, object]:
    return {
        "contract": COLLABORATIVE_WORKSPACE_CONTRACT,
        "workspace_id": workspace_id,
        "roles": {"reference": "ref", "agent_workbench": "agent-workbench"},
    }


def _complete_unit(root: Path, stem: str = "memo", *, private: bool = False) -> None:
    root.mkdir(parents=True)
    agents, claude = navigation_bytes()
    (root / "AGENTS.md").write_bytes(agents)
    (root / "CLAUDE.md").write_bytes(claude)
    (root / f"{stem}.md").write_bytes(b"# prepared\n")
    (root / "assets").mkdir()
    (root / "assets/.keep").write_bytes(b"")
    (root / "src").mkdir()
    (root / "src/.keep").write_bytes(b"")
    if private:
        (root / "record.json").write_bytes(b"{}\n")


def _inner_manifest(
    workspace_id: str,
    ref: Path,
    source_path: str | None = None,
    *,
    source_kind: str = "file",
    quality: str = "ready",
    generation: int = 1,
) -> dict[str, object]:
    records: list[dict[str, str]] = []
    items: list[dict[str, object]] = []
    warnings: list[str] = []
    if source_path is not None:
        prepared_digest = canonical_tree_digest(ref / Path(source_path))
        source_digest = prepared_digest if source_kind == "knowledge_unit" else HEX_A
        records.append({"path": source_path, "kind": source_kind, "digest": source_digest})
        issues: list[str] = []
        if quality == "ready_with_warnings":
            issues = ["conversion reported a recoverable warning"]
            warnings = ["Review warning-bearing prepared items against their source"]
        items.append(
            {
                "source_path": source_path,
                "source_kind": source_kind,
                "source_digest": source_digest,
                "unit_path": source_path,
                "prepared_digest": prepared_digest,
                "provider_route": "knowledge-unit-copy" if source_kind == "knowledge_unit" else "file-conversion",
                "quality": quality,
                "issues": issues,
            }
        )
    return {
        "contract": AGENT_WORKBENCH_CONTRACT,
        "workspace_id": workspace_id,
        "generation": generation,
        "quality": quality,
        "source_records": records,
        "source_tree_digest": source_records_digest(records),
        "items": items,
        "warnings": warnings,
    }


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    result: dict[str, tuple[str, bytes | None]] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[relative] = ("directory", None) if path.is_dir() else ("file", path.read_bytes())
    return result


class CollaborativeWorkspaceCoreTests(unittest.TestCase):
    def test_version_and_extension_discovery_preserve_legacy_command_list(self) -> None:
        legacy = dispatch("capabilities", {})
        self.assertEqual(VERSION, "1.2.1")
        self.assertEqual(legacy["data"]["commands"], ["capabilities", "inspect", "validate", "repair", "stage.complete"])
        self.assertEqual(
            legacy["data"]["extensions"]["collaborative_workspace"]["capabilities_command"],
            "collaborative_workspace.capabilities",
        )

        result = dispatch("collaborative_workspace.capabilities", {})
        self.assertEqual(set(result), RESULT_FIELDS)
        self.assertEqual((result["abi"], result["status"], result["exit_code"]), (ABI, "ok", 0))
        self.assertEqual(
            result["data"]["commands"],
            [
                "collaborative_workspace.capabilities",
                "collaborative_workspace.inspect",
                "collaborative_workspace.validate",
                "collaborative_workspace.stage.complete",
            ],
        )
        self.assertFalse(result["data"]["mutation_boundary"]["creates_manifests_or_payloads"])
        self.assertFalse(result["data"]["mutation_boundary"]["moves_or_publishes_roots"])
        self.assertTrue(result["data"]["mutation_boundary"]["adds_missing_navigation_and_fixed_directories_only"])
        self.assertEqual(
            result["data"]["fixed_paths"][COLLABORATIVE_WORKSPACE_CONTRACT]["outdated"],
            "ref/_outdated",
        )
        self.assertEqual(
            result["data"]["fixed_paths"][AGENT_WORKBENCH_CONTRACT]["outdated"],
            "ref/_outdated",
        )

    def test_workspace_requests_require_exact_path_and_contract(self) -> None:
        requests = [
            {"path": str(ROOT)},
            {"path": str(ROOT), "contract": "unknown/v1"},
            {"path": "relative", "contract": COLLABORATIVE_WORKSPACE_CONTRACT},
            {"path": str(ROOT), "contract": COLLABORATIVE_WORKSPACE_CONTRACT, "extra": True},
        ]
        for request in requests:
            with self.subTest(request=request):
                result = dispatch("collaborative_workspace.inspect", request)
                self.assertEqual((result["status"], result["exit_code"]), ("usage_error", 2))

    def test_outer_stage_completion_is_manifest_preserving_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace-stage"
            root.mkdir()
            workspace_id = str(uuid.uuid4())
            manifest_bytes = json.dumps(_outer_manifest(workspace_id)).encode("utf-8")
            (root / "collaborative-workspace.json").write_bytes(manifest_bytes)

            result = dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual((result["status"], result["exit_code"]), ("ok", 0))
            self.assertEqual(result["data"]["workspace_id"], workspace_id)
            self.assertEqual(
                result["data"]["changes"],
                ["AGENTS.md", "CLAUDE.md", "ref/", "agent-workbench/", "ref/_outdated/"],
            )
            self.assertEqual((root / "collaborative-workspace.json").read_bytes(), manifest_bytes)
            self.assertEqual((root / "CLAUDE.md").read_bytes(), b"@AGENTS.md\n")
            self.assertTrue((root / "ref" / "_outdated").is_dir())

            before = _snapshot(root)
            again = dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual((again["status"], again["data"]["changes"]), ("ok", []))
            self.assertEqual(before, _snapshot(root))

    def test_stage_completion_never_creates_manifest_or_mutates_invalid_stage(self) -> None:
        for contract in (COLLABORATIVE_WORKSPACE_CONTRACT, AGENT_WORKBENCH_CONTRACT):
            with self.subTest(contract=contract), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "stage"
                root.mkdir()
                before = _snapshot(root)
                result = dispatch("collaborative_workspace.stage.complete", _request(root, contract))
                self.assertEqual((result["status"], result["exit_code"]), ("validation_error", 3))
                self.assertIn("missing_manifest", {issue["code"] for issue in result["issues"]})
                self.assertEqual(before, _snapshot(root))

    def test_stage_completion_refuses_tampered_fixed_file_before_other_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace-stage"
            root.mkdir()
            _write_json(root / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            (root / "AGENTS.md").write_bytes(b"tampered\n")
            before = _snapshot(root)
            result = dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual((result["status"], result["exit_code"]), ("validation_error", 3))
            self.assertIn("navigation_guide_mismatch", {issue["code"] for issue in result["issues"]})
            self.assertEqual(before, _snapshot(root))

    def test_valid_nested_projection_reuses_knowledge_unit_v2_and_full_basename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            source_path = "group/report.docx"
            _complete_unit(ref / "group" / "report.docx", "report.docx")
            workspace_id = str(uuid.uuid4())
            manifest = _inner_manifest(workspace_id, ref, source_path)
            _write_json(ref / ".agent-workbench.json", manifest)

            completed = dispatch("collaborative_workspace.stage.complete", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((completed["status"], completed["exit_code"]), ("ok", 0))
            self.assertEqual(
                completed["data"]["changes"],
                ["AGENTS.md", "CLAUDE.md", "temp/", "output/", "ref/_outdated/"],
            )
            validated = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((validated["status"], validated["data"]["quality"]), ("ok", "ready"))
            self.assertEqual(validated["data"]["items"][0]["unit_path"], source_path)
            self.assertEqual(validated["data"]["source_tree_digest"], source_records_digest(manifest["source_records"]))

    def test_outer_outdated_is_required_safe_flexible_and_not_active_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            _write_json(root / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            completed = dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual(completed["status"], "ok")

            archive = root / "ref" / "_outdated"
            (archive / "human-layout" / "old").mkdir(parents=True)
            (archive / "human-layout" / "old" / "memo.bin").write_bytes(b"retired")
            before = _snapshot(root)
            validated = dispatch("collaborative_workspace.validate", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual((validated["status"], validated["data"]["valid"]), ("ok", True))
            self.assertEqual(before, _snapshot(root))

            (archive / "human-layout" / "CLAUDE.local.md").write_bytes(b"not data")
            invalid = dispatch("collaborative_workspace.validate", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertIn("instruction_control_path", {issue["code"] for issue in invalid["issues"]})

    def test_inner_outdated_accepts_strict_generation_batches_with_valid_units(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            _complete_unit(ref / "current.docx", "current.docx")
            manifest = _inner_manifest(str(uuid.uuid4()), ref, "current.docx", generation=3)
            _write_json(ref / ".agent-workbench.json", manifest)
            self.assertEqual(
                dispatch("collaborative_workspace.stage.complete", _request(root, AGENT_WORKBENCH_CONTRACT))["status"],
                "ok",
            )

            archived = ref / "_outdated" / "generation-1-20260901T0730Z" / "group" / "old.docx"
            _complete_unit(archived, "old.docx")
            before = _snapshot(root)
            validated = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((validated["status"], validated["data"]["valid"]), ("ok", True))
            self.assertEqual(before, _snapshot(root))

    def test_inner_outdated_rejects_invalid_batches_generations_and_non_ku_leaves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            _write_json(ref / ".agent-workbench.json", _inner_manifest(str(uuid.uuid4()), ref, generation=3))
            self.assertEqual(
                dispatch("collaborative_workspace.stage.complete", _request(root, AGENT_WORKBENCH_CONTRACT))["status"],
                "ok",
            )
            outdated = ref / "_outdated"
            _complete_unit(outdated / "generation-1-20260901T0730Z" / "one", "one")
            _complete_unit(outdated / "generation-1-20260901T0731Z" / "two", "two")
            _complete_unit(outdated / "generation-3-20260901T0732Z" / "three", "three")
            (outdated / "generation-2-20261301T0730Z" / "raw").mkdir(parents=True)
            (outdated / "generation-2-20261301T0730Z" / "raw" / "not-a-ku.bin").write_bytes(b"raw")
            (outdated / "bad-batch" / "empty").mkdir(parents=True)

            result = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            codes = {issue["code"] for issue in result["issues"]}
            self.assertTrue(
                {
                    "duplicate_outdated_generation",
                    "invalid_outdated_generation",
                    "invalid_outdated_batch",
                    "outdated_container_not_directory",
                    "empty_outdated_container",
                }
                <= codes
            )

    def test_warning_projection_is_valid_but_inconsistent_quality_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            _complete_unit(ref / "scan.pdf", "scan.pdf")
            manifest = _inner_manifest(str(uuid.uuid4()), ref, "scan.pdf", quality="ready_with_warnings")
            _write_json(ref / ".agent-workbench.json", manifest)
            self.assertEqual(
                dispatch("collaborative_workspace.stage.complete", _request(root, AGENT_WORKBENCH_CONTRACT))["status"],
                "ok",
            )

            manifest["quality"] = "ready"
            _write_json(ref / ".agent-workbench.json", manifest)
            result = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((result["status"], result["exit_code"]), ("validation_error", 3))
            self.assertIn("quality_issue_mismatch", {issue["code"] for issue in result["issues"]})

    def test_private_knowledge_unit_projection_is_validated_with_its_declared_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            _complete_unit(ref / "saved-note", private=True)
            manifest = _inner_manifest(str(uuid.uuid4()), ref, "saved-note", source_kind="knowledge_unit")
            _write_json(ref / ".agent-workbench.json", manifest)
            result = dispatch("collaborative_workspace.stage.complete", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((result["status"], result["exit_code"]), ("ok", 0))

    def test_manifest_digest_mapping_and_unexpected_projection_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            _complete_unit(ref / "report.pdf", "report.pdf")
            manifest = _inner_manifest(str(uuid.uuid4()), ref, "report.pdf")
            manifest["source_tree_digest"] = "0" * 64
            manifest["items"][0]["prepared_digest"] = "1" * 64
            _write_json(ref / ".agent-workbench.json", manifest)
            (ref / "undeclared.bin").write_bytes(b"extra")

            before = _snapshot(root)
            inspected = dispatch("collaborative_workspace.inspect", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((inspected["status"], inspected["data"]["valid"]), ("ok", False))
            codes = {issue["code"] for issue in inspected["issues"]}
            self.assertTrue({"source_tree_digest_mismatch", "prepared_digest_mismatch", "unexpected_projection_entry"} <= codes)
            self.assertEqual(before, _snapshot(root))

    def test_canonical_collisions_and_prefix_collisions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            records = [
                {"path": "A", "kind": "file", "digest": HEX_A},
                {"path": "a", "kind": "file", "digest": HEX_A},
                {"path": "folder", "kind": "file", "digest": HEX_A},
                {"path": "folder/child", "kind": "file", "digest": HEX_A},
            ]
            manifest = {
                "contract": AGENT_WORKBENCH_CONTRACT,
                "workspace_id": str(uuid.uuid4()),
                "generation": 1,
                "quality": "ready",
                "source_records": records,
                "source_tree_digest": source_records_digest(records),
                "items": [],
                "warnings": [],
            }
            _write_json(ref / ".agent-workbench.json", manifest)
            result = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            codes = {issue["code"] for issue in result["issues"]}
            self.assertIn("path_collision", codes)
            self.assertIn("path_prefix_collision", codes)
            self.assertIn("projection_inventory_mismatch", codes)

    def test_ascii_del_is_rejected_in_manifest_and_projected_entry_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent-workbench"
            ref = root / "ref"
            ref.mkdir(parents=True)
            source_path = "report\u007f.pdf"
            _complete_unit(ref / source_path)
            _write_json(ref / ".agent-workbench.json", _inner_manifest(str(uuid.uuid4()), ref, source_path))
            agents, claude = workspace_navigation_bytes(AGENT_WORKBENCH_CONTRACT)
            (root / "AGENTS.md").write_bytes(agents)
            (root / "CLAUDE.md").write_bytes(claude)
            (root / "temp").mkdir()
            (root / "output").mkdir()

            result = dispatch("collaborative_workspace.validate", _request(root, AGENT_WORKBENCH_CONTRACT))
            self.assertEqual((result["status"], result["exit_code"]), ("validation_error", 3))
            self.assertIn("invalid_relative_path", {issue["code"] for issue in result["issues"]})
            invalid_entries = [issue["path"] for issue in result["issues"] if issue["code"] == "invalid_entry_name"]
            self.assertEqual(invalid_entries, [f"ref/{source_path}"])

    def test_outer_ref_rejects_instruction_controls_but_accepts_valid_units(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            _write_json(root / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            self.assertEqual(
                dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))["status"],
                "ok",
            )
            _complete_unit(root / "ref" / "reference-unit")
            (root / "ref" / "ordinary").mkdir()
            (root / "ref" / "ordinary" / "CLAUDE.local.md").write_bytes(b"do not trust")

            result = dispatch("collaborative_workspace.validate", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual(result["status"], "validation_error")
            control_paths = [issue["path"] for issue in result["issues"] if issue["code"] == "instruction_control_path"]
            self.assertEqual(control_paths, ["ref/ordinary/CLAUDE.local.md"])
            self.assertNotIn("invalid_knowledge_unit", {issue["code"] for issue in result["issues"]})

    def test_valid_knowledge_unit_cannot_use_an_instruction_control_directory_as_its_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary) / "workspace"
            outer.mkdir()
            _write_json(outer / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            self.assertEqual(
                dispatch("collaborative_workspace.stage.complete", _request(outer, COLLABORATIVE_WORKSPACE_CONTRACT))["status"],
                "ok",
            )
            _complete_unit(outer / "ref" / ".claude")
            outer_result = dispatch("collaborative_workspace.validate", _request(outer, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertIn("instruction_control_path", {issue["code"] for issue in outer_result["issues"]})

            inner = Path(temporary) / "agent-workbench"
            inner_ref = inner / "ref"
            inner_ref.mkdir(parents=True)
            source_path = ".cursor/reference-unit"
            _complete_unit(inner_ref / source_path)
            manifest = _inner_manifest(str(uuid.uuid4()), inner_ref, source_path, source_kind="knowledge_unit")
            _write_json(inner_ref / ".agent-workbench.json", manifest)
            inner_result = dispatch("collaborative_workspace.inspect", _request(inner, AGENT_WORKBENCH_CONTRACT))
            self.assertFalse(inner_result["data"]["valid"])
            self.assertIn("instruction_control_path", {issue["code"] for issue in inner_result["issues"]})

    def test_outer_manifest_is_exact_and_uuidv4_is_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            manifest = _outer_manifest(str(uuid.uuid1()))
            manifest["extra"] = True
            _write_json(root / "collaborative-workspace.json", manifest)
            result = dispatch("collaborative_workspace.inspect", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            codes = {issue["code"] for issue in result["issues"]}
            self.assertIn("invalid_manifest_schema", codes)
            self.assertIn("invalid_workspace_id", codes)

    def test_jsonl_runner_exposes_extension_without_changing_result_shape(self) -> None:
        wrappers = [
            {"command": "capabilities", "request": {}},
            {"command": "collaborative_workspace.capabilities", "request": {}},
            {
                "command": "collaborative_workspace.inspect",
                "request": {"path": str(ROOT / "missing"), "contract": COLLABORATIVE_WORKSPACE_CONTRACT},
            },
        ]
        payload = b"".join(json.dumps(wrapper).encode("utf-8") + b"\n" for wrapper in wrappers)
        completed = subprocess.run(
            [sys.executable, "-I", "-S", str(RUNNER)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        results = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([result["command"] for result in results], [wrapper["command"] for wrapper in wrappers])
        self.assertTrue(all(set(result) == RESULT_FIELDS for result in results))
        self.assertEqual(results[0]["data"]["commands"], ["capabilities", "inspect", "validate", "repair", "stage.complete"])

    @unittest.skipUnless(os.name == "nt", "Windows long-path evidence")
    def test_workspace_commands_accept_normal_absolute_path_beyond_max_path(self) -> None:
        temporary = Path(tempfile.mkdtemp(prefix="anti-entropy-core-workspace-long-"))
        try:
            parent = temporary
            counter = 0
            while len(str(parent / "workspace")) <= 280:
                parent /= f"segment-{counter:02d}-" + "x" * 24
                counter += 1
            root = parent / "workspace"
            native_root = native_path(root)
            native_root.mkdir(parents=True)
            _write_json(native_root / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            self.assertGreater(len(str(root)), 260)
            self.assertFalse(str(root).startswith("\\\\?\\"))

            completed = dispatch("collaborative_workspace.stage.complete", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            validated = dispatch("collaborative_workspace.validate", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertEqual((completed["status"], validated["status"]), ("ok", "ok"))
            self.assertEqual((completed["data"]["path"], validated["data"]["path"]), (str(root), str(root)))
        finally:
            shutil.rmtree(native_path(temporary))

    def test_workspace_navigation_resources_are_contract_specific_and_fixed(self) -> None:
        outer_agents, outer_claude = workspace_navigation_bytes(COLLABORATIVE_WORKSPACE_CONTRACT)
        inner_agents, inner_claude = workspace_navigation_bytes(AGENT_WORKBENCH_CONTRACT)
        self.assertNotEqual(outer_agents, inner_agents)
        self.assertIn(b"human-owned reference tree", outer_agents)
        self.assertIn(b"prepared, read-only projection", inner_agents)
        self.assertEqual(outer_claude, b"@AGENTS.md\n")
        self.assertEqual(inner_claude, b"@AGENTS.md\n")
        self.assertEqual(hashlib.sha256(outer_claude).hexdigest(), hashlib.sha256(inner_claude).hexdigest())
        self.assertEqual(hashlib.sha256(outer_agents).hexdigest(), "9a435b118dfd6ad62db4992f64687f8caefc073cbde65ab23b05332b8f224e24")
        self.assertEqual(hashlib.sha256(inner_agents).hexdigest(), "8f247d6828b67419be6025c1772d3be9cdba9d1498d36b6ddcdc4b0cb365efbc")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support unavailable")
    def test_workspace_link_is_rejected_when_host_allows_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "workspace"
            root.mkdir()
            _write_json(root / "collaborative-workspace.json", _outer_manifest(str(uuid.uuid4())))
            outside = Path(temporary) / "outside"
            outside.mkdir()
            try:
                os.symlink(outside, root / "ref", target_is_directory=True)
            except OSError:
                self.skipTest("current token cannot create symlinks")
            (root / "agent-workbench").mkdir()
            outer_agents, outer_claude = workspace_navigation_bytes(COLLABORATIVE_WORKSPACE_CONTRACT)
            (root / "AGENTS.md").write_bytes(outer_agents)
            (root / "CLAUDE.md").write_bytes(outer_claude)
            result = dispatch("collaborative_workspace.validate", _request(root, COLLABORATIVE_WORKSPACE_CONTRACT))
            self.assertIn("link_not_allowed", {issue["code"] for issue in result["issues"]})


if __name__ == "__main__":
    unittest.main()
