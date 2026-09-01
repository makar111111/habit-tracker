"""Разова міграція: додати користувачів до вже існуючої бази.

Навіщо це взагалі потрібно
--------------------------
create_db_and_tables() уміє тільки СТВОРЮВАТИ таблиці, яких ще немає.
Змінити вже існуючу вона не може — і це правильно, інакше вона могла б
непомітно знищити дані. Тому коли в моделі з'являється нове поле,
стару базу треба переробити вручну. Цей крок називається міграцією.

У справжніх проєктах для цього беруть Alembic: він веде історію змін
схеми й уміє відкочувати їх назад. Тут одна разова зміна, тому
достатньо простого скрипта — але знати про Alembic варто.

Запуск (з папки проєкту, сервер має бути зупинений):

    .venv\\Scripts\\python.exe migrate_add_users.py

Скрипт спершу робить копію бази, тож зіпсувати дані він не може.
Запускати двічі безпечно: побачивши, що все вже зроблено, він вийде.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

DB = Path("habits.db")
BACKUP = Path("habits.db.backup")


def already_migrated(db: sqlite3.Connection) -> bool:
    """Чи є в таблиці habit колонка user_id."""
    columns = [row[1] for row in db.execute("PRAGMA table_info(habit)")]
    return "user_id" in columns


def main() -> int:
    if not DB.exists():
        print("Бази habits.db немає — мігрувати нічого.")
        print("Просто запусти сервер: він створить нову базу вже з користувачами.")
        return 0

    db = sqlite3.connect(DB)

    if already_migrated(db):
        print("База вже має колонку user_id. Нічого робити не треба.")
        return 0

    habits = db.execute("SELECT count(*) FROM habit").fetchone()[0]
    checkins = db.execute("SELECT count(*) FROM checkin").fetchone()[0]
    print(f"Знайдено: {habits} звичок, {checkins} відміток.")

    db.close()
    shutil.copy2(DB, BACKUP)
    print(f"Копію збережено у {BACKUP}")

    db = sqlite3.connect(DB)
    try:
        # Одна транзакція на всі зміни: або все вдається, або база
        # лишається такою, як була. Проміжного стану не буває.
        with db:
            # 1. Таблиця користувачів. Пишемо SQL руками, бо SQLModel тут
            #    не імпортуємо: скрипт має працювати, навіть якщо моделі
            #    в майбутньому зміняться ще раз.
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS user (
                    id INTEGER NOT NULL PRIMARY KEY,
                    telegram_id INTEGER,
                    name VARCHAR NOT NULL
                )
                """
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_telegram_id "
                "ON user (telegram_id)"
            )

            # 2. Локальний користувач — той, від чийого імені працює браузер.
            #    Усі наявні звички належать йому: до появи бота інших не було.
            db.execute(
                "INSERT INTO user (telegram_id, name) VALUES (NULL, ?)",
                ("Локальний користувач",),
            )
            local_user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

            # 3. Тепер найцікавіше. SQLite не вміє додавати колонку
            #    NOT NULL до непорожньої таблиці — їй ніде взяти значення
            #    для вже наявних рядків. Тому стандартний хід: створити
            #    таблицю правильної форми, перелити в неї дані, стару
            #    прибрати, нову перейменувати.
            #
            #    id зберігаємо як є — на них посилаються відмітки.
            db.execute(
                """
                CREATE TABLE habit_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    description VARCHAR NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES user (id)
                )
                """
            )
            db.execute(
                "INSERT INTO habit_new (id, name, description, user_id) "
                "SELECT id, name, description, ? FROM habit",
                (local_user_id,),
            )
            db.execute("DROP TABLE habit")
            db.execute("ALTER TABLE habit_new RENAME TO habit")
            db.execute("CREATE INDEX ix_habit_user_id ON habit (user_id)")

        # 4. Перевірка, що нічого не загубилось. Міграція без перевірки —
        #    це надія, а не інженерія.
        habits_after = db.execute("SELECT count(*) FROM habit").fetchone()[0]
        checkins_after = db.execute("SELECT count(*) FROM checkin").fetchone()[0]
        orphans = db.execute(
            "SELECT count(*) FROM checkin "
            "WHERE habit_id NOT IN (SELECT id FROM habit)"
        ).fetchone()[0]

        if (habits_after, checkins_after, orphans) != (habits, checkins, 0):
            print("ПОМИЛКА: дані не збіглися після міграції.")
            print(f"  звичок {habits} -> {habits_after}")
            print(f"  відміток {checkins} -> {checkins_after}")
            print(f"  відміток-сиріт: {orphans}")
            print(f"Поверни базу з копії: {BACKUP}")
            return 1

        print(f"Готово. {habits} звичок і {checkins} відміток збережено.")
        print("Усі вони належать локальному користувачеві вебінтерфейсу.")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
