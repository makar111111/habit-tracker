"""Разова міграція: перенести звички локального користувача в Telegram-акаунт.

Кому потрібно
-------------
Тим, хто користувався трекером у браузері ДО того, як з'явився бот.
Тоді всі звички належали «локальному користувачеві» (той, у кого
telegram_id порожній), а після появи бота Telegram завів окремий акаунт —
і виходило два непов'язані світи в одній базі.

Цей скрипт зливає перший у другий: звички змінюють власника, відмітки
їдуть за ними самі (вони прив'язані до звички, а не до людини).

Запуск (сервер краще зупинити, щоб ніхто не писав у базу паралельно):

    .venv\\Scripts\\python.exe migrate_merge_local_user.py            # показати план
    .venv\\Scripts\\python.exe migrate_merge_local_user.py --apply    # виконати

Без --apply нічого не змінюється: спершу дивишся, що буде, і лише потім
погоджуєшся. Копія бази робиться автоматично перед змінами.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

DB = Path("habits.db")
BACKUP = Path("habits.db.before-merge")


def find_users(db: sqlite3.Connection) -> tuple[int | None, list[tuple[int, int, str]]]:
    """Повернути id локального користувача і список telegram-акаунтів."""
    local = db.execute("SELECT id FROM user WHERE telegram_id IS NULL").fetchone()
    telegram = db.execute(
        "SELECT id, telegram_id, name FROM user WHERE telegram_id IS NOT NULL"
    ).fetchall()
    return (local[0] if local else None), telegram


def habits_of(db: sqlite3.Connection, user_id: int) -> list[tuple[int, str, int]]:
    """Звички користувача разом із кількістю відміток у кожній."""
    return db.execute(
        """
        SELECT h.id, h.name, (SELECT count(*) FROM checkin c WHERE c.habit_id = h.id)
        FROM habit h WHERE h.user_id = ? ORDER BY h.id
        """,
        (user_id,),
    ).fetchall()


def main() -> int:
    apply = "--apply" in sys.argv

    if not DB.exists():
        print("Бази habits.db немає — переносити нічого.")
        return 0

    db = sqlite3.connect(DB)
    local_id, telegram_users = find_users(db)

    if local_id is None:
        print("Локального користувача немає — переносити нічого.")
        return 0

    if not telegram_users:
        print("У базі немає жодного Telegram-акаунта.")
        print("Спершу напиши боту /start — акаунт створиться сам.")
        return 1

    if len(telegram_users) > 1:
        # Здогадуватись, кому саме віддати чужі дані, скрипт не має права.
        print("У базі кілька Telegram-акаунтів — не можу вирішити сам:")
        for uid, tg, name in telegram_users:
            print(f"  id={uid} telegram_id={tg} {name!r}")
        print("\nПеренеси вручну або залиш лише потрібний акаунт.")
        return 1

    target_id, target_tg, target_name = telegram_users[0]

    moving = habits_of(db, local_id)
    if not moving:
        print("У локального користувача немає звичок — переносити нічого.")
        return 0

    existing = habits_of(db, target_id)

    print(f"Куди: {target_name!r} (telegram_id={target_tg})\n")
    print("Переносимо:")
    total_checkins = 0
    for hid, name, count in moving:
        total_checkins += count
        print(f"  • {name!r} — {count} відміток")
    print(f"\nРазом: {len(moving)} звичок, {total_checkins} відміток")

    if existing:
        print("\nУже є в цьому акаунті:")
        for hid, name, count in existing:
            print(f"  • {name!r} — {count} відміток")

    # Однакові назви — не помилка й не конфлікт для бази, але майже
    # завжди означає, що людина двічі завела ту саму звичку, не знаючи
    # про першу. Попереджаємо, щоб це не спливло несподівано.
    duplicates = {n.lower() for _, n, _ in moving} & {
        n.lower() for _, n, _ in existing
    }
    if duplicates:
        print(f"\nУВАГА: однакові назви в обох акаунтах: {sorted(duplicates)}")
        print("Скрипт їх НЕ зливає — після перенесення будуть обидві.")

    if not apply:
        print("\n--- це був лише план, нічого не змінено ---")
        print("Щоб виконати: додай --apply")
        return 0

    db.close()
    shutil.copy2(DB, BACKUP)
    print(f"\nКопію збережено у {BACKUP}")

    db = sqlite3.connect(DB)
    try:
        # Одна транзакція: або переїжджають усі звички, або жодна.
        with db:
            db.execute(
                "UPDATE habit SET user_id = ? WHERE user_id = ?", (target_id, local_id)
            )

        # Перевірка, а не надія: рахуємо, чи все справді на місці.
        left = habits_of(db, local_id)
        now = habits_of(db, target_id)
        orphans = db.execute(
            "SELECT count(*) FROM checkin WHERE habit_id NOT IN (SELECT id FROM habit)"
        ).fetchone()[0]

        if left or len(now) != len(moving) + len(existing) or orphans:
            print("ПОМИЛКА: після міграції дані не збіглися.")
            print(f"  лишилось у локального: {len(left)}")
            print(f"  стало в цільового: {len(now)}, очікували {len(moving) + len(existing)}")
            print(f"  відміток-сиріт: {orphans}")
            print(f"Поверни базу з копії: {BACKUP}")
            return 1

        moved_checkins = sum(c for _, _, c in now)
        print(f"\nГотово. У {target_name!r} тепер {len(now)} звичок, "
              f"{moved_checkins} відміток.")
        print("Локальний користувач лишився в базі порожнім — це нормально:")
        print("він знадобиться, якщо колись знову ввімкнеш ALLOW_LOCAL_USER.")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
