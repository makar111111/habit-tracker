"""Резервування лише тимчасових SQLite-файлів, включно з активним WAL."""

import importlib
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path
from threading import Barrier

import pytest
from sqlmodel import create_engine


def make_database(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO example VALUES (7, 'початкові дані');"
            "PRAGMA user_version = 3;"
        )
        connection.commit()


def rows(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT * FROM example ORDER BY id").fetchall()


def test_backup_captures_committed_wal_and_restore_preserves_rows(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    make_database(source)

    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO example VALUES (8, 'дані в WAL')")
        writer.commit()
        writer.execute("INSERT INTO example VALUES (9, 'ще не збережено')")
        assert backups.backup_database(source, snapshot) == snapshot
        writer.rollback()

    assert rows(snapshot) == [(7, "початкові дані"), (8, "дані в WAL")]
    assert backups.restore_database(snapshot, restored) == restored
    assert rows(restored) == rows(snapshot)
    with closing(sqlite3.connect(restored)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize("operation", ["backup_database", "restore_database"])
def test_existing_destination_is_never_overwritten(tmp_path, operation):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "existing.sqlite3"
    make_database(source)
    destination.write_bytes(b"must survive")

    with pytest.raises(FileExistsError):
        getattr(backups, operation)(source, destination)

    assert destination.read_bytes() == b"must survive"
    assert rows(source) == [(7, "початкові дані")]


@pytest.mark.parametrize("operation", ["backup_database", "restore_database"])
def test_source_cannot_be_its_own_destination(tmp_path, operation):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    make_database(source)

    with pytest.raises((ValueError, FileExistsError)):
        getattr(backups, operation)(source, source)

    assert rows(source) == [(7, "початкові дані")]


def test_missing_source_creates_no_files(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "missing.sqlite3"
    destination = tmp_path / "snapshot.sqlite3"

    with pytest.raises(FileNotFoundError):
        backups.backup_database(source, destination)

    assert not source.exists()
    assert not destination.exists()


def test_invalid_source_does_not_leave_a_restore_file(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "invalid.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    source.write_text("this is not a database", encoding="utf-8")

    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        backups.restore_database(source, destination)

    assert not destination.exists()
    assert source.read_text(encoding="utf-8") == "this is not a database"


def test_empty_source_is_not_accepted_as_a_valid_backup(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "empty.sqlite3"
    source.touch()
    destination = tmp_path / "restored.sqlite3"

    with pytest.raises(ValueError, match="empty|порож"):
        backups.restore_database(source, destination)

    assert not destination.exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_destination_with_existing_sqlite_sidecar_is_rejected(tmp_path, suffix):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "restored.sqlite3"
    make_database(source)
    sidecar = tmp_path / (destination.name + suffix)
    sidecar.write_bytes(b"unrelated existing state")

    with pytest.raises(FileExistsError):
        backups.restore_database(source, destination)

    assert not destination.exists()
    assert sidecar.read_bytes() == b"unrelated existing state"


def test_cli_requires_explicit_source_and_destination():
    backups = importlib.import_module("backups")
    for arguments in [[], ["backup"], ["restore"], ["backup", "--source", "named.sqlite3"]]:
        with pytest.raises(SystemExit) as error:
            backups.main(arguments)
        assert error.value.code == 2


def test_cli_backup_then_restore(tmp_path, capsys):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    make_database(source)

    assert backups.main(["backup", "--source", str(source), "--destination", str(snapshot)]) == 0
    assert backups.main(["restore", "--source", str(snapshot), "--destination", str(restored)]) == 0
    assert rows(restored) == rows(source)
    assert str(restored) in capsys.readouterr().out


def test_daily_snapshot_is_once_per_day_without_overwrite(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    make_database(source)
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    try:
        first = backups.daily_backup(engine, tmp_path / "snapshots", today=date(2026, 9, 12))
        assert first is not None
        assert rows(first) == [(7, "початкові дані")]
        with closing(sqlite3.connect(source)) as connection:
            connection.execute("INSERT INTO example VALUES (8, 'наступний день')")
            connection.commit()
        assert backups.daily_backup(engine, tmp_path / "snapshots", today=date(2026, 9, 12)) is None
        assert rows(first) == [(7, "початкові дані")]
        second = backups.daily_backup(engine, tmp_path / "snapshots", today=date(2026, 9, 13))
        assert second is not None
        assert rows(second) == rows(source)
    finally:
        engine.dispose()


def test_daily_snapshot_skips_in_memory_database(tmp_path):
    backups = importlib.import_module("backups")
    engine = create_engine("sqlite://")
    try:
        assert backups.daily_backup(engine, tmp_path / "snapshots") is None
        assert not (tmp_path / "snapshots").exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("content", [b"broken backup", b""])
def test_daily_snapshot_does_not_accept_corrupt_existing_copy(tmp_path, content):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    make_database(source)
    engine = create_engine(f"sqlite:///{source.as_posix()}")
    try:
        snapshot = backups.daily_backup(engine, tmp_path / "snapshots", today=date(2026, 9, 12))
        snapshot.write_bytes(content)
        with pytest.raises((sqlite3.DatabaseError, ValueError)):
            backups.daily_backup(engine, tmp_path / "snapshots", today=date(2026, 9, 12))
        assert snapshot.read_bytes() == content
    finally:
        engine.dispose()


def test_destination_is_published_only_after_validation_and_sync(tmp_path, monkeypatch):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "snapshot.sqlite3"
    make_database(source)
    real_fsync = os.fsync
    observed = []

    def before_publication(descriptor):
        observed.append(True)
        assert not destination.exists(), "final name must not expose unfinished work"
        real_fsync(descriptor)

    monkeypatch.setattr(backups.os, "fsync", before_publication)
    backups.backup_database(source, destination)
    assert observed
    assert rows(destination) == rows(source)


def test_crash_leaves_only_staging_and_daily_retry_succeeds(tmp_path):
    backups = importlib.import_module("backups")
    source = tmp_path / "source.sqlite3"
    directory = tmp_path / "snapshots"
    directory.mkdir()
    destination = directory / "source.sqlite3.daily-2026-09-12.sqlite3"
    make_database(source)
    # Окремий процес завершується без finally, як при kill/втраті живлення.
    code = """
import os
import sqlite3
import sys
from pathlib import Path
import backups
real_connect = sqlite3.connect
def crash_on_target(database, *args, **kwargs):
    if not kwargs.get('uri'):
        Path(database).write_bytes(b'partial snapshot')
        os._exit(17)
    return real_connect(database, *args, **kwargs)
backups.sqlite3.connect = crash_on_target
backups.backup_database(sys.argv[1], sys.argv[2])
"""
    environment = dict(os.environ, PYTHON_DOTENV_DISABLED="1", DATABASE_URL="sqlite://")
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), str(destination)],
        cwd=Path(__file__).parent, env=environment, capture_output=True, timeout=15,
    )
    assert result.returncode == 17, result.stderr.decode(errors="replace")
    assert not destination.exists()
    leftovers = list(directory.iterdir())
    assert len(leftovers) == 1
    assert leftovers[0].read_bytes() == b"partial snapshot"

    engine = create_engine(f"sqlite:///{source.as_posix()}")
    try:
        assert backups.daily_backup(engine, directory, today=date(2026, 9, 12)) == destination
        assert rows(destination) == rows(source)
        assert leftovers[0].read_bytes() == b"partial snapshot"  # не наше поточне staging
        assert backups.daily_backup(engine, directory, today=date(2026, 9, 12)) is None
    finally:
        engine.dispose()


def test_concurrent_publication_never_overwrites_the_winner(tmp_path, monkeypatch):
    backups = importlib.import_module("backups")
    first = tmp_path / "first.sqlite3"
    second = tmp_path / "second.sqlite3"
    destination = tmp_path / "snapshot.sqlite3"
    make_database(first)
    make_database(second)
    with closing(sqlite3.connect(second)) as connection:
        connection.execute("UPDATE example SET value='інша копія'")
        connection.commit()
    barrier = Barrier(2)
    real_link = os.link
    attempted = []

    def publish_together(source, target):
        attempted.append(source)
        barrier.wait(timeout=5)
        real_link(source, target)

    monkeypatch.setattr(backups.os, "link", publish_together)

    def backup(source):
        try:
            return source, backups.backup_database(source, destination)
        except FileExistsError:
            return source, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(backup, [first, second]))
    assert len(attempted) == 2
    winners = [source for source, result in results if result is not None]
    assert len(winners) == 1
    assert rows(destination) == rows(winners[0])
    assert set(tmp_path.iterdir()) == {first, second, destination}
