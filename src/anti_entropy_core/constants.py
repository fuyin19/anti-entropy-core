from __future__ import annotations

from importlib.resources import files

ABI = "anti-entropy-core.runner/v1"
VERSION = "1.2.0"
ENVELOPE = "knowledge-unit-envelope/v2"
NAVIGATION_CONTRACT = "knowledge-unit-navigation/v1"
COMMANDS = ("capabilities", "inspect", "validate", "repair", "stage.complete")
COLLABORATIVE_WORKSPACE_CONTRACT = "collaborative-workspace-envelope/v1"
AGENT_WORKBENCH_CONTRACT = "agent-workbench-envelope/v1"
COLLABORATIVE_WORKSPACE_COMMANDS = (
    "collaborative_workspace.capabilities",
    "collaborative_workspace.inspect",
    "collaborative_workspace.validate",
    "collaborative_workspace.stage.complete",
)
WORKSPACE_CONTRACTS = (COLLABORATIVE_WORKSPACE_CONTRACT, AGENT_WORKBENCH_CONTRACT)
PRIVATE_ROOT_SETS = ((), ("record.json",))


def navigation_bytes() -> tuple[bytes, bytes]:
    root = files("anti_entropy_core").joinpath("resources")
    return root.joinpath("AGENTS.md").read_bytes(), root.joinpath("CLAUDE.md").read_bytes()


def workspace_navigation_bytes(contract: str) -> tuple[bytes, bytes]:
    root = files("anti_entropy_core").joinpath("resources")
    if contract == COLLABORATIVE_WORKSPACE_CONTRACT:
        agents_name = "COLLABORATIVE_WORKSPACE_AGENTS.md"
    elif contract == AGENT_WORKBENCH_CONTRACT:
        agents_name = "AGENT_WORKBENCH_AGENTS.md"
    else:
        raise ValueError(f"unknown workspace contract: {contract}")
    return root.joinpath(agents_name).read_bytes(), root.joinpath("CLAUDE.md").read_bytes()


__all__ = [
    "ABI",
    "AGENT_WORKBENCH_CONTRACT",
    "COLLABORATIVE_WORKSPACE_COMMANDS",
    "COLLABORATIVE_WORKSPACE_CONTRACT",
    "COMMANDS",
    "ENVELOPE",
    "NAVIGATION_CONTRACT",
    "PRIVATE_ROOT_SETS",
    "VERSION",
    "WORKSPACE_CONTRACTS",
    "navigation_bytes",
    "workspace_navigation_bytes",
]
