"""Послідовні версії SQLite-схеми з перевіркою та копією перед змінами."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine
from sqlmodel import SQLModel

from backups import backup_database

CURRENT_VERSION = 1

_BASE_COLUMNS = {
    "user": {"id", "telegram_id", "name"},
    "habit": {"id", "user_id", "name", "description"},
    "checkin": {"id", "habit_id", "day"},
    "logintoken": {"token", "created_at", "user_id"},
}
_V1_COLUMNS = {
    "user": {
        "timezone": "VARCHAR NOT NULL DEFAULT 'Europe/Kyiv'",
        "reminder_hour": "INTEGER NOT NULL DEFAULT 20",
        "reminders_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "last_reminder_day": "DATE",
    },
    "habit": {
        "start_date": "DATE",
        "weekdays": "JSON NOT NULL DEFAULT '[0,1,2,3,4,5,6]'",
        "archived_at": "DATE",
    },
}


class MigrationError(RuntimeError):
    """Схема або дані не дозволяють безпечно продовжити оновлення."""


@dataclass(frozen=True)
class MigrationResult:
    from_version: int
    to_version: int
    backup_path: Path | None = None


def _verify_schema(connection: Connection, *, current: bool) -> None:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table, base_columns in _BASE_COLUMNS.items():
        required = base_columns | set(_V1_COLUMNS.get(table, {})) if current else base_columns
        if table not in tables:
            # У старих випусках до вебвходу цієї таблиці могло не бути.
            if table == "logintoken" and not current:
                continue
            raise MigrationError(f"Unsupported SQLite schema: missing table {table}")
        missing = required - {column["name"] for column in inspector.get_columns(table)}
        if missing:
            raise MigrationError(f"Unsupported SQLite schema: {table} lacks {sorted(missing)}")


def _verify_data(connection: Connection) -> None:
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").all()
    if integrity != [("ok",)]:
        raise MigrationError(f"SQLite integrity check failed: {integrity!r}")
    foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if foreign_keys:
        raise MigrationError(f"SQLite foreign key check failed: {foreign_keys!r}")


def _upgrade_to_v1(connection: Connection) -> None:
    for table, columns in _V1_COLUMNS.items():
        existing = {column["name"] for column in inspect(connection).get_columns(table)}
        for name, definition in columns.items():
            if name not in existing:
                # Імена та визначення є константами коду, не даними запиту.
                connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')
    today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
    connection.exec_driver_sql(
        """UPDATE habit SET start_date = COALESCE(
            (SELECT MIN(day) FROM checkin WHERE checkin.habit_id = habit.id), ?
        ) WHERE start_date IS NULL""",
        (today,),
    )


def upgrade(engine: Engine) -> MigrationResult:
    """Створити нову або атомарно оновити підтримувану стару базу.

    BEGIN IMMEDIATE серіалізує старти API та утримує інших записувачів
    під час знімка. Окреме read-only з'єднання SQLite backup бачить
    останній збережений стан. Помилка копії зупиняє зміни схеми.
    """
    if engine.dialect.name != "sqlite":
        raise MigrationError("Schema migrations support only SQLite databases")
    # Завантажити моделі без імпорту database: це працює також для CLI,
    # тестів і виклику database.create_db_and_tables без попереднього main.
    import models  # noqa: F401

    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            if version > CURRENT_VERSION:
                raise MigrationError(
                    f"Database schema version {version} is newer than supported {CURRENT_VERSION}"
                )
            if version < 0:
                raise MigrationError(f"Invalid database schema version: {version}")
            if version == CURRENT_VERSION:
                _verify_schema(connection, current=True)
                _verify_data(connection)
                connection.commit()
                return MigrationResult(version, version)

            existing_tables = inspect(connection).get_table_names()
            snapshot = None
            if existing_tables:
                _verify_schema(connection, current=False)
                _verify_data(connection)
                filename = next(
                    row[2] for row in connection.exec_driver_sql("PRAGMA database_list")
                    if row[1] == "main"
                )
                if filename:
                    source = Path(filename).resolve()
                    directory = source.parent / "backups"
                    directory.mkdir(parents=True, exist_ok=True)
                    # Коротка унікальна назва працює також у вкладених
                    # Windows-теках; O_EXCL у backup не дає перезаписати.
                    destination = directory / f"{source.name}.pre-v{CURRENT_VERSION}-{uuid4().hex[:12]}.sqlite3"
                    snapshot = backup_database(source, destination)

            SQLModel.metadata.create_all(connection)
            if version < 1:
                _upgrade_to_v1(connection)
            _verify_schema(connection, current=True)
            _verify_data(connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {CURRENT_VERSION}")
            connection.commit()
            return MigrationResult(version, CURRENT_VERSION, snapshot)
        except BaseException:
            connection.rollback()
            raise
