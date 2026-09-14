"""Безпечний запуск Claude Code у headless-режимі (`claude -p`).

В інтерактиві Claude питає дозволу перед небезпечною дією. У headless
питати нема кого, тож межі задаються ДО запуску, і тільки тут:

  * жорсткий allow-list інструментів за профілем (`--allowedTools`);
  * `--permission-mode dontAsk` — усе, чого немає в списку, мовчки
    відхиляється, а не зависає в очікуванні відповіді;
  * `--max-turns` і `--max-budget-usd` — зациклений таск зупиниться сам;
  * жодних `--bare` чи обходу дозволів: вони вимикають хуки-запобіжники.

Приклади:
    python run_headless.py "знайди TODO у frontend/src"
    python run_headless.py --profile edit --max-turns 25 "виправ падіння test_stats.py"
    python run_headless.py --dry-run "..."   # лише показати команду
"""

import argparse
import shutil
import subprocess
import sys

READ_ONLY = ["Read", "Glob", "Grep"]

PROFILES: dict[str, list[str]] = {
    # Аналіз і рев'ю: нічого не змінює на диску.
    "read": READ_ONLY,
    # Правки коду плюс перевірка результату. Shell — лише конкретні
    # команди за префіксом, а не «будь-який Bash».
    "edit": READ_ONLY
    + [
        "Edit",
        "Write",
        "Bash(python -m pytest:*)",
        "Bash(python -m ruff:*)",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(npm test:*)",
        "Bash(npm run lint:*)",
    ],
}

# Заборонено завжди, навіть якщо хтось розширить профіль: публікація
# назовні й мережа — дії, які в автономному режимі виконує лише людина.
ALWAYS_DENIED = ["Bash(git push:*)", "Bash(git commit:*)", "WebFetch", "WebSearch"]

DEFAULT_MAX_TURNS = 15
MAX_TURNS_CEILING = 50
DEFAULT_BUDGET_USD = 1.0


def build_command(
    prompt: str,
    profile: str = "read",
    max_turns: int = DEFAULT_MAX_TURNS,
    budget_usd: float = DEFAULT_BUDGET_USD,
    claude: str = "claude",
) -> list[str]:
    """Збирає argv для `claude -p`. Некоректні межі — ValueError, а не запуск."""
    if profile not in PROFILES:
        raise ValueError(f"невідомий профіль {profile!r}; є: {', '.join(PROFILES)}")
    if not 1 <= max_turns <= MAX_TURNS_CEILING:
        raise ValueError(f"max_turns має бути від 1 до {MAX_TURNS_CEILING}")
    if budget_usd <= 0:
        raise ValueError("бюджет має бути додатним")
    if not prompt.strip():
        raise ValueError("порожній промпт")

    return [
        claude,
        "-p",
        prompt,
        "--permission-mode",
        "dontAsk",
        # Промпт стоїть ДО списків: --allowedTools приймає кілька значень
        # поспіль і «проковтнув» би промпт, стоячи після нього.
        "--allowedTools",
        *PROFILES[profile],
        "--disallowedTools",
        *ALWAYS_DENIED,
        "--max-turns",
        str(max_turns),
        "--max-budget-usd",
        str(budget_usd),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt")
    parser.add_argument("--profile", choices=PROFILES, default="read")
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    claude = shutil.which("claude") or "claude"
    try:
        command = build_command(
            args.prompt, args.profile, args.max_turns, args.budget, claude
        )
    except ValueError as error:
        parser.error(str(error))

    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return 0
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
