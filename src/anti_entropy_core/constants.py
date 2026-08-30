from __future__ import annotations

from importlib.resources import files

ABI = "anti-entropy-core.runner/v1"
VERSION = "1.0.0"
ENVELOPE = "knowledge-unit-envelope/v2"
NAVIGATION_CONTRACT = "knowledge-unit-navigation/v1"
COMMANDS = ("capabilities", "inspect", "validate", "repair", "stage.complete")
PRIVATE_ROOT_SETS = ((), ("record.json",))


def navigation_bytes() -> tuple[bytes, bytes]:
    root = files("anti_entropy_core").joinpath("resources")
    return root.joinpath("AGENTS.md").read_bytes(), root.joinpath("CLAUDE.md").read_bytes()


__all__ = [
    "ABI",
    "COMMANDS",
    "ENVELOPE",
    "NAVIGATION_CONTRACT",
    "PRIVATE_ROOT_SETS",
    "VERSION",
    "navigation_bytes",
]

