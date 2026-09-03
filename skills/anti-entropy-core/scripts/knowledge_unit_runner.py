#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from anti_entropy_core.model import Issue  # noqa: E402
from anti_entropy_core.protocol import dispatch_wrapper  # noqa: E402
from anti_entropy_core.result import make_result  # noqa: E402


def _decode(raw: bytes) -> object:
    return json.loads(raw.decode("utf-8"))


def _write(value: dict[str, object]) -> None:
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> int:
    if len(sys.argv) != 1:
        _write(
            make_result(
                "request",
                "usage_error",
                2,
                issues=[Issue("argv", "JSONL runner accepts no command-line arguments")],
            )
        )
        return 2
    while True:
        raw = sys.stdin.buffer.readline()
        if raw == b"":
            return 0
        try:
            if not raw.endswith(b"\n"):
                raise ValueError("JSONL input must end with LF")
            value = _decode(raw[:-1])
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            _write(
                make_result(
                    "request",
                    "usage_error",
                    2,
                    issues=[Issue("jsonl", str(exc))],
                )
            )
            continue
        _write(dispatch_wrapper(value))


if __name__ == "__main__":
    raise SystemExit(main())
