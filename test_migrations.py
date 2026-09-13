"""Версійні міграції на старій схемі у свіжих тимчасових файлах."""

import importlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect
from sqlalchemy import create_mock_engine
from sqlmodel import create_engine


@pytest.fixture
def old_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE user (id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE, name VARCHAR NOT NULL);
            CREATE TABLE habit (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES user(id),
                                name VARCHAR NOT NULL, description VARCHAR NOT NULL);
            CREATE TABLE checkin (id INTEGER PRIMARY KEY, habit_id INTEGER NOT NULL REFERENCES habit(id),
                                  day DATE NOT NULL, UNIQUE(habit_id, day));
            CREATE TABLE logintoken (token VARCHAR PRIMARY KEY, created_at DATETIME NOT NULL,
                                     user_id INTEGER REFERENCES user(id));
            INSERT INTO user VALUES (41, 700001, 'Олена'), (42, NULL, 'Локальний');
            INSERT INTO habit VALUES (101, 41, 'Читання', 'Щодня'), (102, 42, 'Рух', '');
            INSERT INTO checkin VALUES (301, 101, '2025-01-03'), (302, 101, '2025-01-01');
            INSERT INTO logintoken VALUES ('test-token', '2026-09-01 10:00:00', 41);
            """
        )
        connection.commit()
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    yield engine, path
    engine.dispose()


def test_upgrade_preserves_old_data_and_takes_pre_upgrade_snapshot(old_database):
    migrations = importlib.import_module("migrations")
    engine, path = old_database
    before_today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()

    result = migrations.upgrade(engine)

    after_today = datetime.now(ZoneInfo("Europe/Kyiv")).date().isoformat()
    assert result.from_version == 0
    assert result.to_version == migrations.CURRENT_VERSION == 1
    assert result.backup_path is not None
    assert result.backup_path != path
    with closing(sqlite3.connect(result.backup_path)) as snapshot:
        assert snapshot.execute("PRAGMA user_version").fetchone() == (0,)
        assert "weekdays" not in {row[1] for row in snapshot.execute("PRAGMA table_info(habit)")}
        assert snapshot.execute("SELECT * FROM checkin ORDER BY id").fetchall() == [
            (301, 101, "2025-01-03"), (302, 101, "2025-01-01")
        ]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT id, telegram_id, name FROM user ORDER BY id").all() == [
            (41, 700001, "Олена"), (42, None, "Локальний")
        ]
        assert connection.exec_driver_sql("SELECT id, user_id, name, description FROM habit ORDER BY id").all() == [
            (101, 41, "Читання", "Щодня"), (102, 42, "Рух", "")
        ]
        assert connection.exec_driver_sql("SELECT timezone, reminder_hour, reminders_enabled, last_reminder_day FROM user").all() == [
            ("Europe/Kyiv", 20, 1, None), ("Europe/Kyiv", 20, 1, None)
        ]
        habits = connection.exec_driver_sql("SELECT start_date, weekdays, archived_at FROM habit ORDER BY id").all()
        assert habits[0][0] == "2025-01-01"
        assert habits[1][0] in {before_today, after_today}
        assert all(json.loads(row[1]) == list(range(7)) and row[2] is None for row in habits)
        assert connection.exec_driver_sql("SELECT * FROM checkin ORDER BY id").all() == [
            (301, 101, "2025-01-03"), (302, 101, "2025-01-01")
        ]
        assert connection.exec_driver_sql("SELECT * FROM logintoken").all() == [("test-token", "2026-09-01 10:00:00", 41)]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_repeat_upgrade_is_noop_and_preserves_user_choices(old_database):
    migrations = importlib.import_module("migrations")
    engine, _ = old_database
    first = migrations.upgrade(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE user SET timezone='UTC', reminder_hour=9, reminders_enabled=0 WHERE id=41")
        connection.exec_driver_sql("UPDATE habit SET weekdays='[0,2,4]', archived_at='2026-09-01' WHERE id=101")

    repeated = migrations.upgrade(engine)

    assert repeated.from_version == repeated.to_version == 1
    assert repeated.backup_path is None
    assert list(first.backup_path.parent.iterdir()) == [first.backup_path]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT timezone, reminder_hour, reminders_enabled FROM user WHERE id=41").one() == ("UTC", 9, 0)
        assert connection.exec_driver_sql("SELECT weekdays, archived_at FROM habit WHERE id=101").one() == ("[0,2,4]", "2026-09-01")


@pytest.mark.parametrize("file_database", [False, True])
def test_new_database_is_created_at_current_version_without_backup(tmp_path, file_database):
    migrations = importlib.import_module("migrations")
    engine = create_engine(f"sqlite:///{(tmp_path / 'new.sqlite3').as_posix()}" if file_database else "sqlite://")
    try:
        result = migrations.upgrade(engine)
        assert result.backup_path is None
        assert result.to_version == 1
        assert {"user", "habit", "checkin", "logintoken"} <= set(inspect(engine).get_table_names())
        assert {"start_date", "weekdays", "archived_at"} <= {column["name"] for column in inspect(engine).get_columns("habit")}
        assert {"timezone", "reminder_hour", "reminders_enabled", "last_reminder_day"} <= {column["name"] for column in inspect(engine).get_columns("user")}
        assert not (tmp_path / "backups").exists()
    finally:
        engine.dispose()


def test_backup_failure_aborts_before_any_schema_change(old_database, monkeypatch):
    migrations = importlib.import_module("migrations")
    engine, _ = old_database

    def fail_backup(*args, **kwargs):
        raise OSError("backup storage unavailable")

    monkeypatch.setattr(migrations, "backup_database", fail_backup)
    with pytest.raises(OSError, match="backup storage unavailable"):
        migrations.upgrade(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 0
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM checkin").scalar_one() == 2
        assert "weekdays" not in {column["name"] for column in inspect(connection).get_columns("habit")}


def test_newer_schema_is_rejected_unchanged(old_database):
    migrations = importlib.import_module("migrations")
    engine, path = old_database
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=99")

    with pytest.raises(migrations.MigrationError, match="newer|новіш"):
        migrations.upgrade(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 99
    assert not (path.parent / "backups").exists()


def test_broken_foreign_keys_abort_upgrade_and_rollback(old_database):
    migrations = importlib.import_module("migrations")
    engine, _ = old_database
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("INSERT INTO checkin VALUES (303, 9999, '2025-01-02')")

    with pytest.raises(migrations.MigrationError, match="foreign|зв'яз"):
        migrations.upgrade(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 0
        assert "weekdays" not in {column["name"] for column in inspect(connection).get_columns("habit")}


def test_version_one_with_missing_schema_is_rejected(old_database):
    migrations = importlib.import_module("migrations")
    engine, _ = old_database
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version=1")

    with pytest.raises(migrations.MigrationError, match="schema|схем"):
        migrations.upgrade(engine)


def test_failed_verification_rolls_back_already_applied_columns(old_database, monkeypatch):
    migrations = importlib.import_module("migrations")
    engine, path = old_database
    verify_schema = migrations._verify_schema

    def fail_after_changes(connection, *, current):
        if current:
            assert "weekdays" in {column["name"] for column in inspect(connection).get_columns("habit")}
            raise migrations.MigrationError("injected post-migration validation failure")
        verify_schema(connection, current=current)

    monkeypatch.setattr(migrations, "_verify_schema", fail_after_changes)
    with pytest.raises(migrations.MigrationError, match="post-migration"):
        migrations.upgrade(engine)

    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 0
        assert "weekdays" not in {column["name"] for column in inspect(connection).get_columns("habit")}
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM checkin").scalar_one() == 2
    assert len(list((path.parent / "backups").iterdir())) == 1


def test_unsupported_dialect_is_rejected_without_connecting():
    migrations = importlib.import_module("migrations")
    engine = create_mock_engine("postgresql://", lambda *args: pytest.fail("must not connect"))
    with pytest.raises(migrations.MigrationError, match="SQLite"):
        migrations.upgrade(engine)
