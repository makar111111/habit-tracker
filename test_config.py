"""Перевірка налаштувань без імпорту БД або читання локального .env."""

import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "sqlite:///habits.db"),
        ("   ", "sqlite:///habits.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_empty_database_url_uses_documented_default(monkeypatch, value, expected):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DATABASE_URL", value)
    settings = runpy.run_path(str(Path(__file__).with_name("config.py")))
    assert settings["DATABASE_URL"] == expected
