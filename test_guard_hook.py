"""Тести хука-запобіжника .claude/hooks/guard_command.py."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / ".claude" / "hooks" / "guard_command.py"

# Тека .claude — не пакет Python, тож модуль підключаємо за шляхом.
_spec = importlib.util.spec_from_file_location("guard_command", HOOK)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize(
    "command",
    [
        "git push --force",
        "git push origin main -f",
        "git push -uf origin main",
        "git push origin +main",
        "git reset --hard HEAD~1",
        "git clean -fd",
        "git checkout -- .",
        "git restore .",
        "rm -rf frontend/dist",
        "rm -r node_modules",
        "rm --recursive build",
        "Remove-Item -Recurse -Force frontend\\dist",
        "rmdir /s /q dist",
        "sqlite3 habits.db 'DELETE FROM habits'",
        "cp habits.db.backup habits.db",
        "python -c \"import sqlite3; sqlite3.connect('habits.db')\"",
        "rm .env",
        "echo BOT_TOKEN=x > .env",
        "Set-Content .env 'SECRET=1'",
        "docker compose down -v",
        "docker compose down --volumes",
        "docker volume rm habits_data",
        "docker system prune -a",
        "python migrate.py --env=prod",
        "python migrate.py --env production",
        "git status && git push --force",
    ],
)
def test_blocks_dangerous_commands(command):
    assert guard.find_danger(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git push origin main",
        "git push --force-with-lease",
        "git reset --soft HEAD~1",
        "git reset HEAD file.py",
        "git clean -n",
        "git restore --staged main.py",
        "git checkout -- main.py",
        "rm frontend/src/lib/__probe.ts",
        "rm -f __probe.py",
        "Remove-Item __probe.py",
        "cat .env.example",
        "cp .env.example .env.example.bak",
        "python -m pytest -q",
        "npm run lint",
        "docker compose down",
        "docker compose logs -f bot",
        "git status; rm -f stale.txt",
        "python main.py --env=dev",
    ],
)
def test_allows_safe_commands(command):
    assert guard.find_danger(command) is None


def run_hook(command: str) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hook_exits_2_with_reason_for_dangerous_command():
    result = run_hook("git reset --hard")

    assert result.returncode == 2
    assert "заблоковано" in result.stderr
    assert "reset --hard" in result.stderr


def test_hook_exits_0_for_safe_command():
    result = run_hook("git status")

    assert result.returncode == 0
    assert result.stderr == ""
