"""Хук PreToolUse: блокує небезпечні shell-команди ДО їх виконання.

Claude Code передає на stdin JSON із командою (інструменти Bash і
PowerShell). Якщо команда збігається з одним із правил нижче, скрипт
пише причину в stderr і завершується з кодом 2 — Claude Code скасовує
виклик, а Claude бачить пояснення. Будь-який інший код виходу дозволяє
команді виконатися.

Правила — це регулярні вирази, а не розбір shell-синтаксису: вони
навмисно трохи надмірні. Хибна тривога коштує одного перефразування
команди, пропущена — втраченої бази.
"""

import json
import re
import sys

# Межа «аргументів однієї команди»: не перестрибуємо через ; && || |,
# щоб `git status; rm -f x` не вважалося прапорцем git.
ARGS = r"[^;&|\n]*"

# Ім'я .env, але НЕ .env.example (його комітити можна й треба).
DOTENV = r"(?<![\w.])\.env(?![\w.])"

RULES: list[tuple[str, str]] = [
    (
        rf"\bgit\b{ARGS}\bpush\b{ARGS}(--force(?!-with-lease)\b|\s-[a-zA-Z]*f\b|\s\+\S)",
        "force push переписує історію на сервері. Якщо це справді потрібно, "
        "попроси користувача виконати команду самостійно.",
    ),
    (
        rf"\bgit\b{ARGS}\breset\b{ARGS}--hard\b",
        "git reset --hard безповоротно знищує незакомічені зміни.",
    ),
    (
        rf"\bgit\b{ARGS}\bclean\b{ARGS}\s-[a-zA-Z]*f",
        "git clean -f видаляє невідстежувані файли — серед них .env і habits.db.",
    ),
    (
        rf"\bgit\b{ARGS}\b(checkout\s+--|restore)\s+\.(\s|$)",
        "ця команда відкидає ВСІ незакомічені зміни в робочому дереві.",
    ),
    (
        rf"\brm\b{ARGS}\s(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b",
        "рекурсивне видалення. Видаляй конкретні файли на ім'я.",
    ),
    (
        rf"\b(Remove-Item|rmdir|rd|del|erase)\b{ARGS}\s(-Recurse|/s)\b",
        "рекурсивне видалення. Видаляй конкретні файли на ім'я.",
    ),
    (
        r"habits\.db",
        "за правилами проєкту жоден скрипт не торкається habits.db та його копій "
        "напряму. Для перевірок підміняй базу через "
        "app.dependency_overrides[get_session].",
    ),
    (
        rf"(\b(rm|del|erase|mv|move|cp|copy|Remove-Item|Move-Item|Copy-Item|"
        rf"Set-Content|Add-Content|Out-File|Clear-Content|New-Item)\b{ARGS}{DOTENV}"
        rf"|>>?\s*[\"']?{DOTENV})",
        ".env містить секрети й не редагується автоматично. Зміни в налаштуваннях "
        "описуй у .env.example і попроси користувача перенести їх.",
    ),
    (
        rf"\bdocker\b{ARGS}\bdown\b{ARGS}\s(-v|--volumes)\b",
        "база в Docker живе у volume — down --volumes видалить її разом із даними.",
    ),
    (
        r"\bdocker\b[^;&|\n]*\b(volume\s+(rm|prune)|system\s+prune)\b",
        "видалення Docker-томів знищить базу контейнера.",
    ),
    (
        # claude саме як КОМАНДА (початок або після ; & |, можливо зі шляхом),
        # а не слово в тексті: інакше блокувався б коміт, що про це розповідає.
        rf"(?:^|[;&|]\s*)(?:\S*[\\/])?claude(?:\.exe)?\s{ARGS}"
        rf"(--dangerously-skip-permissions|bypassPermissions|--bare\b)",
        "вкладений запуск claude в обхід дозволів або з --bare вимикає хуки-запобіжники. "
        "Для автономного запуску є run_headless.py.",
    ),
    (
        r"--env[=\s]+(prod|production)\b",
        "команда спрямована на продакшн. Такі дії виконує лише людина.",
    ),
]

COMPILED = [(re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in RULES]


def find_danger(command: str) -> str | None:
    """Повертає причину блокування або None, якщо команда безпечна."""
    for pattern, reason in COMPILED:
        if pattern.search(command):
            return reason
    return None


def main() -> int:
    # На Windows stdin/stderr за замовчуванням у cp1251, а Claude Code
    # говорить UTF-8 — без цього кирилиця в поясненні стає «кракозябрами».
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    command = payload.get("tool_input", {}).get("command", "")

    reason = find_danger(command)
    if reason is None:
        return 0

    print(f"Команду заблоковано хуком-запобіжником: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
