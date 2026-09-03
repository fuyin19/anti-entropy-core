#!/usr/bin/env python3
"""Forward the legacy source path to the single maintained skill runner."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    runner = Path(__file__).absolute().parents[1] / "skills" / "anti-entropy-core" / "scripts" / "knowledge_unit_runner.py"
    runpy.run_path(str(runner), run_name="__main__")
