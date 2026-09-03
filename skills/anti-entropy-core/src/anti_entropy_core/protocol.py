from __future__ import annotations

import json
from typing import Any

from .constants import COLLABORATIVE_WORKSPACE_COMMANDS, COMMANDS
from .envelope import inspect_envelope
from .model import Issue, RequestError, ValidationFailure
from .operations import capabilities, parse_path_request, repair_stage, validate_unit
from .result import make_result
from .workspace import (
    collaborative_workspace_capabilities,
    complete_workspace_stage,
    inspect_workspace,
    parse_workspace_request,
    validate_workspace,
)


def dispatch(command: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        if command not in (*COMMANDS, *COLLABORATIVE_WORKSPACE_COMMANDS):
            raise RequestError(f"unknown command: {command}")
        if command == "collaborative_workspace.capabilities":
            if request:
                raise RequestError("collaborative_workspace.capabilities request must be empty")
            return make_result(command, "ok", 0, data=collaborative_workspace_capabilities())
        if command in COLLABORATIVE_WORKSPACE_COMMANDS:
            path, contract = parse_workspace_request(request)
            if command == "collaborative_workspace.inspect":
                inspection = inspect_workspace(path, contract)
                return make_result(command, "ok", 0, data=inspection.data(), issues=inspection.issues)
            if command == "collaborative_workspace.validate":
                inspection = validate_workspace(path, contract)
                return make_result(command, "ok", 0, data=inspection.data())
            inspection, changes = complete_workspace_stage(path, contract)
            data = inspection.data()
            data["changes"] = changes
            data["completed"] = True
            return make_result(command, "ok", 0, data=data)
        if command == "capabilities":
            if request:
                raise RequestError("capabilities request must be empty")
            return make_result(command, "ok", 0, data=capabilities())
        path, private = parse_path_request(request)
        if command == "inspect":
            inspection = inspect_envelope(path, private)
            return make_result(command, "ok", 0, data=inspection.data(), issues=inspection.issues)
        if command == "validate":
            inspection = validate_unit(path, private)
            return make_result(command, "ok", 0, data=inspection.data())
        inspection, changes = repair_stage(path, private)
        data = inspection.data()
        data["changes"] = changes
        if command == "stage.complete":
            data["completed"] = True
        return make_result(command, "ok", 0, data=data)
    except RequestError as exc:
        return make_result(
            command,
            "usage_error",
            2,
            issues=[Issue("request", str(exc))],
        )
    except ValidationFailure as exc:
        return make_result(
            command,
            "validation_error",
            3,
            data=exc.inspection.data(),
            issues=exc.inspection.issues,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return make_result(
            command,
            "io_error",
            6,
            issues=[Issue("io", f"Local filesystem operation failed ({type(exc).__name__})")],
        )


def dispatch_wrapper(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"command", "request"}:
        return make_result(
            "request",
            "usage_error",
            2,
            issues=[Issue("wrapper", "wrapper must contain exactly command and request")],
        )
    command = value["command"]
    request = value["request"]
    if not isinstance(command, str) or not command or not isinstance(request, dict):
        return make_result(
            command if isinstance(command, str) and command else "request",
            "usage_error",
            2,
            issues=[Issue("wrapper", "command must be non-empty and request must be an object")],
        )
    return dispatch(command, request)


__all__ = ["dispatch", "dispatch_wrapper"]
