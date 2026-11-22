"""Tests for the Claude Code guard hook in .claude/hooks (AGENTS.md section 9)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_bash.py"


def _decide(command: str, cwd: Path) -> str | None:
    payload = {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git push -u origin claude/feature", None),
        ("git push --force-with-lease=b:origin/b -u origin b", None),
        ("git checkout maintenance", None),
        ("git tag -l", None),
        ("git push origin main", "deny"),
        ("git push origin HEAD:main", "deny"),
        ("git push --force origin feature", "deny"),
        ("git tag v1.0.0", "ask"),
        ("git push origin --tags", "ask"),
        ("gh release create v1.0.0", "ask"),
    ],
)
def test_guard_git_commands(command: str, expected: str | None) -> None:
    """Pushes to main and plain force pushes are denied; tags and releases ask the human."""
    assert _decide(command, Path.cwd()) == expected


def _repo(tmp_path: Path, content: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "data.py").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "data.py"], check=True)
    return tmp_path


def test_guard_rejects_commit_with_unknown_sensor_id(tmp_path: Path) -> None:
    """A staged 12-hex value that is not a fixture ID blocks the commit."""
    # Built at runtime so this test file itself never trips the guard.
    fake_id = "0A1B2C" + "3D4E5F"
    repo = _repo(tmp_path, f'SENSOR = "{fake_id}"\n')
    assert _decide("git commit -m test", repo) == "deny"


def test_guard_allows_commit_with_fixture_ids(tmp_path: Path) -> None:
    """Anonymised fixture IDs and allowlisted examples pass."""
    repo = _repo(tmp_path, 'IDS = ["020000AABB02", "AAAAAAAAAAAA"]\n')
    assert _decide("git commit -m test", repo) is None
