#!/usr/bin/env python3
"""PreToolUse hook for Bash: enforce the protection rules that must never depend on memory.

- deny pushes to `main`/`master` and plain `--force` pushes (AGENTS.md section 8)
- ask the human before tags and GitHub releases (AGENTS.md section 8, release skill)
- deny commits whose staged diff adds sensor-ID-shaped tokens that are not
  anonymised fixture IDs (AGENTS.md section 5.8)

Stdlib only. Any unexpected problem lets the command through: a broken guard must not
block work, and CI plus review still apply.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HEX12 = re.compile(r"(?<![0-9A-Za-z])[0-9A-Fa-f]{12}(?![0-9A-Za-z])")
# Anonymised fixture IDs: <prefix>0000<6 hex>, as written by scripts/anonymize_dump.py.
FIXTURE_ID = re.compile(r"^[0-9A-F]{2}0000[0-9A-F]{6}$")
ALLOWLIST = Path(__file__).with_name("allowed_ids.txt")


def _decision(kind: str, reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": kind,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def _allowed_ids() -> set[str]:
    if not ALLOWLIST.exists():
        return set()
    return {
        line.split("#", 1)[0].strip().upper()
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    }


def _unexpected_ids_in_staged_diff(cwd: str) -> list[str]:
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color", "--", ".", ":!*.png"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=20,
    ).stdout
    allowed = _allowed_ids()
    found: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for token in HEX12.findall(line):
            upper = token.upper()
            if not FIXTURE_ID.match(upper) and upper not in allowed:
                found.add(upper)
    return sorted(found)


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    command = str(data.get("tool_input", {}).get("command", ""))
    cwd = str(data.get("cwd") or ".")

    for push in re.findall(r"git\s+push\b[^;&|\n]*", command):
        if re.search(r"(?<![\w/-])(?:\S+:)?(?:refs/heads/)?(main|master)\b", push):
            _decision("deny", "Never push to main (AGENTS.md section 8). Push the feature branch.")
        if re.search(r"\s(--force|-f)(\s|$)", push):
            _decision(
                "deny", "Plain --force is not allowed; use --force-with-lease on your branch."
            )
        if "--tags" in push:
            _decision("ask", "Pushing tags publishes a release; the maintainer must approve it.")

    if re.search(r"\bgit\s+tag\b(?!\s+(-l|--list)\b)", command) or re.search(
        r"\bgh\s+release\s+create\b|\bgh\s+api\b[^\n]*/releases\b", command
    ):
        _decision(
            "ask", "Tags and releases need the maintainer's explicit go (AGENTS.md section 8)."
        )

    if re.search(r"\bgit\s+commit\b", command):
        unexpected = _unexpected_ids_in_staged_diff(cwd)
        if unexpected:
            masked = ", ".join(f"...{token[-4:]}" for token in unexpected)
            _decision(
                "deny",
                "Staged changes add sensor-ID-shaped values that are not anonymised fixture IDs "
                f"({masked}). Sensor IDs are credentials (AGENTS.md section 5.8): use "
                "scripts/anonymize_dump.py or a <prefix>0000xxxxxx ID. If the value is a known "
                "example, add it to .claude/hooks/allowed_ids.txt with a comment.",
            )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - a broken guard must never block work
        sys.exit(0)
