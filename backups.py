"""Перевірені SQLite-копії; жодного неявного шляху до робочої бази.

CLI: python -m backups backup|restore --source FILE --destination NEW_FILE
"""

import argparse
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.engine import Engine


def _read_only(path: Path) -> sqlite3.Connection:
    if path.stat().st_size == 0:
        raise ValueError(f"Source database is empty: {path}")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)


def _check_integrity(connection: sqlite3.Connection) -> None:
    results = connection.execute("PRAGMA integrity_check").fetchall()
    if results != [("ok",)]:
        raise ValueError(f"SQLite integrity_check failed: {results!r}")


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Скопіювати узгоджений стан (включно з WAL) у НОВИЙ файл.

    Джерело відкривається mode=ro. Запис і перевірка йдуть у власний
    staging-файл. Готова копія атомарно публікується через os.link,
    який не перезаписує наявне призначення навіть у конкурентному виклику.
    Аварійне завершення може лишити staging, але не займає остаточну назву.
    """
    source_path = Path(source).expanduser().resolve(strict=True)
    destination_arg = Path(destination).expanduser()
    # resolve() сам по собі прийняв би dangling symlink як новий шлях.
    if destination_arg.is_symlink():
        raise FileExistsError(f"Destination already exists: {destination_arg}")
    destination_path = destination_arg.resolve()
    if not source_path.is_file():
        raise ValueError(f"Source is not a file: {source_path}")
    if source_path == destination_path:
        raise ValueError("Source and destination must be different files")
    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination_path}")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = destination_path.with_name(destination_path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise FileExistsError(
                f"Destination SQLite sidecar already exists: {sidecar}"
            )

    with closing(_read_only(source_path)) as source_connection:
        _check_integrity(source_connection)
        # Власний файл на тому самому томі дає атомарну публікацію.
        # Немає mkdir(): людина явно вибирає вже наявну теку призначення.
        descriptor, staging_name = tempfile.mkstemp(
            prefix=".backup-", suffix=".sqlite3.tmp", dir=destination_path.parent
        )
        staging = Path(staging_name)
        os.close(descriptor)
        try:
            with closing(sqlite3.connect(staging)) as target_connection:
                deadline = time.monotonic() + 30

                def check_timeout(status: int, remaining: int, total: int) -> None:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "SQLite backup did not finish within 30 seconds"
                        )

                source_connection.backup(
                    target_connection, pages=256, progress=check_timeout, sleep=0.05
                )
                _check_integrity(target_connection)
            # Синхронізація після закриття SQLite; результат повертається,
            # коли завершені всі записи у файл копії.
            with staging.open("r+b") as completed:
                os.fsync(completed.fileno())
            # На відміну від replace, link відмовляє, якщо destination
            # з'явився після перевірки. Файл уже повністю перевірений.
            os.link(staging, destination_path)
        finally:
            # Прибираємо лише staging цього виклику. Наявні копії й
            # залишки попереднього аварійного процесу не змінюються.
            staging.unlink(missing_ok=True)
    return destination_path


def restore_database(source: str | Path, destination: str | Path) -> Path:
    """Перевірити копію та відновити у новий файл, не поверх робочої БД."""
    return backup_database(source, destination)


def sqlite_database_path(engine: Engine) -> Path | None:
    """Реальний шлях main із самого SQLite; memory DB повертає None."""
    if engine.dialect.name != "sqlite":
        raise ValueError("Backups support only SQLite databases")
    with engine.connect() as connection:
        for _, name, filename in connection.exec_driver_sql("PRAGMA database_list"):
            if name == "main":
                return Path(filename).resolve() if filename else None
    return None


def daily_backup(
    engine: Engine, backup_dir: str | Path, *, today: date | None = None
) -> Path | None:
    """Одна копія на UTC-день; наявна копія перевіряється й зберігається.

    Не видаляє попередні копії. Планувальник застосунку викликає функцію
    на старті й щогодини; today дає змогу перевірити дні без очікування.
    """
    if not str(backup_dir).strip():
        return None
    source = sqlite_database_path(engine)
    if source is None:
        return None
    directory = Path(backup_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    day = today or datetime.now(timezone.utc).date()
    destination = directory / f"{source.name}.daily-{day.isoformat()}.sqlite3"
    try:
        return backup_database(source, destination)
    except FileExistsError:
        # Не приховувати пошкоджену копію за повідомленням «вже є».
        with closing(_read_only(destination)) as connection:
            _check_integrity(connection)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("backup", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--source", required=True, type=Path)
        command.add_argument("--destination", required=True, type=Path)
    arguments = parser.parse_args(argv)
    operation = backup_database if arguments.operation == "backup" else restore_database
    try:
        result = operation(arguments.source, arguments.destination)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"{arguments.operation} failed: {error}\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
