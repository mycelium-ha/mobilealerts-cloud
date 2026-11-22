#!/usr/bin/env python3
"""PostToolUse hook for Edit/Write: format a changed Python file with ruff right away.

Keeps `ruff format --check` and import sorting green without a separate fix-up round.
Stdlib only; does nothing when ruff is not installed or the file is outside the repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input", {})
    raw_path = tool_input.get("file_path") or tool_input.get("path")
    ruff = shutil.which("ruff")
    if not raw_path or not str(raw_path).endswith(".py") or ruff is None:
        return
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    path = Path(raw_path).resolve()
    if project not in path.parents or not path.exists():
        return
    for args in (["check", "--fix", "--select", "I", "--quiet"], ["format", "--quiet"]):
        subprocess.run([ruff, *args, str(path)], cwd=project, check=False, timeout=50)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - formatting is a convenience, never a blocker
        sys.exit(0)
