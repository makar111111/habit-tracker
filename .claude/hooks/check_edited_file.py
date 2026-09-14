"""Хук PostToolUse: форматує й лінтить файл одразу після того, як Claude його змінив.

Claude Code передає на stdin JSON із шляхом до файлу. Скрипт робить два
кроки СТРОГО по черзі — хуки однієї події Claude Code запускає паралельно,
тож окремі хуки для форматера й лінтера могли б читати файл, поки інший
його переписує:

1. Форматер (ruff format / Prettier) — мовчки вирівнює стиль.
2. Лінтер (ruff check / ESLint) — якщо знайшов порушення, виводить їх
   у stderr і завершується з кодом 2: так Claude бачить помилку й
   виправляє її сам, а не дізнається про неї лише в CI.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FRONTEND_BIN = FRONTEND / "node_modules" / ".bin"
JS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
# Prettier форматує й те, що ESLint не лінтить.
PRETTIER_ONLY_SUFFIXES = {".css", ".json", ".html", ".md"}


def ruff() -> str:
    exe = ROOT / ".venv" / "Scripts" / "ruff.exe"
    return str(exe) if exe.exists() else "ruff"


def node_tool(name: str) -> str:
    exe = FRONTEND_BIN / f"{name}.cmd"
    return str(exe) if exe.exists() else name


def plan(path: Path) -> tuple[list[str] | None, list[str] | None, Path] | None:
    """Повертає (команда форматера, команда лінтера, робоча тека) або None."""
    if path.suffix == ".py":
        return (
            [ruff(), "format", str(path)],
            [ruff(), "check", str(path)],
            ROOT,
        )
    if FRONTEND not in path.parents:
        return None
    if "node_modules" in path.parts or "dist" in path.parts:
        return None
    formatter = [node_tool("prettier"), "--write", "--log-level", "warn", str(path)]
    if path.suffix in JS_SUFFIXES:
        linter = [node_tool("eslint"), "--max-warnings", "0", str(path)]
        return formatter, linter, FRONTEND
    if path.suffix in PRETTIER_ONLY_SUFFIXES:
        return formatter, None, FRONTEND
    return None


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # .cmd-обгортки npm на Windows запускаються лише через оболонку.
        shell=command[0].endswith(".cmd"),
    )


def main() -> int:
    # На Windows stdin/stderr за замовчуванням у cp1251, а Claude Code
    # говорить UTF-8 — без цього кирилиця в повідомленні стає «кракозябрами».
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path")
    if not file_path:
        return 0

    path = Path(file_path).resolve()
    if not path.exists():
        return 0

    steps = plan(path)
    if steps is None:
        return 0
    formatter, linter, cwd = steps
    relative = path.relative_to(ROOT)

    if formatter is not None:
        result = run(formatter, cwd)
        if result.returncode != 0:
            # Форматер падає лише на синтаксично зламаному файлі — це теж
            # варто показати Claude, а не проковтнути.
            print(
                f"Форматер не зміг обробити {relative}:\n{result.stdout}{result.stderr}",
                file=sys.stderr,
            )
            return 2

    if linter is not None:
        result = run(linter, cwd)
        if result.returncode != 0:
            print(
                f"Лінтер знайшов порушення у {relative} — виправ їх:\n"
                f"{result.stdout}{result.stderr}",
                file=sys.stderr,
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
