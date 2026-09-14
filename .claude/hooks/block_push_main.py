"""Хук PreToolUse: забороняє Claude пушити в main.

Слова «main» у команді недостатньо, щоб вирішити: `git push` без
аргументів відправляє ПОТОЧНУ гілку (push.default = simple), тож на
main він небезпечний, хоча main у ньому не згадано. Тому скрипт розбирає
аргументи push і, коли гілку явно не вказано, питає git про поточну.

Пушити в main людина може сама — хук стосується лише викликів Claude.
"""

import json
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "master"}

# Опції git push, що забирають наступний аргумент як значення —
# інакше `-o ci.skip origin` сприйнявся б як remote «ci.skip».
OPTIONS_WITH_VALUE = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}

# Опції, що пушать усі гілки разом — main серед них.
PUSH_EVERYTHING = {"--all", "--mirror"}


def split_segments(command: str) -> list[list[str]]:
    """Ділить рядок на окремі команди (;, &&, ||, |) і кожну — на токени."""
    segments = []
    for part in re.split(r"&&|\|\||[;|\n]", command):
        try:
            tokens = shlex.split(part, posix=True)
        except ValueError:
            # Незакриті лапки тощо — розбираємо грубо, аби не пропустити push.
            tokens = part.split()
        if tokens:
            segments.append(tokens)
    return segments


def push_arguments(tokens: list[str]) -> list[str] | None:
    """Аргументи після `git ... push` або None, якщо це не git push."""
    if not tokens or tokens[0].lower() not in {"git", "git.exe"}:
        return None
    if "push" not in tokens:
        return None
    return tokens[tokens.index("push") + 1 :]


def target_branches(args: list[str], current_branch: str | None) -> set[str]:
    """Гілки на сервері, які змінить цей push."""
    positional = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in PUSH_EVERYTHING:
            return set(PROTECTED)
        if arg in OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        positional.append(arg)

    refspecs = positional[1:]  # перший позиційний — remote
    if not refspecs:
        return {current_branch} if current_branch else set()

    branches = set()
    for refspec in refspecs:
        destination = refspec.lstrip("+").split(":")[-1]
        if destination == "HEAD" and current_branch:
            destination = current_branch
        branches.add(destination.removeprefix("refs/heads/"))
    return branches


def find_push_to_protected(command: str, current_branch: str | None) -> str | None:
    """Назва захищеної гілки, в яку йде push, або None."""
    for tokens in split_segments(command):
        args = push_arguments(tokens)
        if args is None:
            continue
        hit = target_branches(args, current_branch) & PROTECTED
        if hit:
            return sorted(hit)[0]
    return None


def current_branch(cwd: str | None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.stdout.strip() or None


def main() -> int:
    # На Windows stdin/stderr за замовчуванням у cp1251, а Claude Code
    # говорить UTF-8 — без цього кирилиця в поясненні стає «кракозябрами».
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    # Дешева перевірка спершу: git викликаємо, лише коли в команді є push.
    if not re.search(r"\bpush\b", command):
        return 0

    branch = find_push_to_protected(command, current_branch(payload.get("cwd")))
    if branch is None:
        return 0

    print(
        f"Команду заблоковано: push у гілку {branch} заборонений для Claude. "
        f"Запушити в {branch} користувач може сам; інакше створи окрему гілку "
        "і запуш її.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
