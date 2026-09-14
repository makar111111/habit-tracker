"""Тести хука .claude/hooks/block_push_main.py."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / ".claude" / "hooks" / "block_push_main.py"

# Тека .claude — не пакет Python, тож модуль підключаємо за шляхом.
_spec = importlib.util.spec_from_file_location("block_push_main", HOOK)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.mark.parametrize(
    ("command", "branch"),
    [
        # Гілку названо явно — поточна не важлива.
        ("git push origin main", "feature"),
        ("git push -u origin main", "feature"),
        ("git push origin feature:main", "feature"),
        ("git push origin +feature:main", "feature"),
        ("git push origin HEAD:refs/heads/main", "feature"),
        ("git push origin master", "feature"),
        ("git push --all origin", "feature"),
        ("git push --mirror", "feature"),
        ("git push origin --delete main", "feature"),
        ("git push -o ci.skip origin main", "feature"),
        # Гілку не названо — push іде в поточну, а поточна main.
        ("git push", "main"),
        ("git push origin", "main"),
        ("git push -u origin HEAD", "main"),
        ("git push --force-with-lease", "main"),
        # Push захований у ланцюжку команд.
        ("git add . && git commit -m 'x' && git push", "main"),
        ("git status; git push origin main", "feature"),
    ],
)
def test_blocks_push_to_main(command, branch):
    assert hook.find_push_to_protected(command, branch) is not None


@pytest.mark.parametrize(
    ("command", "branch"),
    [
        ("git push", "feature"),
        ("git push origin feature", "main"),
        ("git push -u origin HEAD", "feature"),
        ("git push origin main:feature", "main"),
        ("git push -o ci.skip origin feature", "main"),
        ("git pull origin main", "main"),
        ("git log origin/main", "main"),
        ("git commit -m 'не пушити в main'", "main"),
        ("npm run lint", "main"),
    ],
)
def test_allows_other_commands(command, branch):
    assert hook.find_push_to_protected(command, branch) is None


@pytest.fixture
def repo(tmp_path):
    """Тимчасовий репозиторій, у якому активна гілка main."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def run_hook(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    )
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hook_reads_current_branch_from_cwd(repo):
    result = run_hook("git push", repo)

    assert result.returncode == 2
    assert "main" in result.stderr


def test_hook_allows_push_from_feature_branch(repo):
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )

    result = run_hook("git push", repo)

    assert result.returncode == 0
    assert result.stderr == ""
