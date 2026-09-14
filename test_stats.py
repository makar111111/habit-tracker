"""Тести обчислення серій.

Тут немає ні бази, ні HTTP — лише функції з stats.py і списки дат.
Тому ці тести виконуються миттєво: їм нічого не треба піднімати.
"""

from datetime import date, timedelta

import pytest

import stats

TODAY = date(2026, 8, 30)


def days_ago(*offsets: int) -> list[date]:
    """days_ago(0, 1) -> [сьогодні, вчора]. Робить тести читабельними."""
    return [TODAY - timedelta(days=n) for n in offsets]


def test_empty_history():
    """Порожня історія не має ламати обчислення."""
    result = stats.compute([], TODAY)

    assert result["total"] == 0
    assert result["current_streak"] == 0
    assert result["longest_streak"] == 0
    assert result["done_today"] is False
    assert result["last_day"] is None


def test_counts_unique_days():
    """Дублікати того самого дня рахуються один раз."""
    result = stats.compute(days_ago(0, 0, 1), TODAY)

    assert result["total"] == 2


def test_reports_last_day():
    """last_day — найсвіжіша відмітка, незалежно від порядку в списку."""
    result = stats.compute(days_ago(5, 0, 3), TODAY)

    assert result["last_day"] == TODAY


# parametrize запускає ту саму функцію кілька разів з різними даними.
# Кожен рядок стає окремим тестом зі своєю назвою у звіті.
@pytest.mark.parametrize(
    "offsets, expected",
    [
        ([], 0),  # нічого не відмічено
        ([0], 1),  # тільки сьогодні
        ([0, 1, 2], 3),  # три дні поспіль
        ([1], 1),  # тільки вчора — пільга, серія жива
        ([1, 2, 3], 3),  # серія до вчора включно
        ([2], 0),  # позавчора — серія вже обірвана
        ([0, 2, 3], 1),  # сьогодні є, вчора немає
        ([0, 1, 10, 11, 12], 2),  # свіжа серія коротша за стару
    ],
)
def test_current_streak(offsets, expected):
    """Поточна серія: рахується назад від сьогодні або вчора."""
    assert stats.current_streak(set(days_ago(*offsets)), TODAY) == expected


@pytest.mark.parametrize(
    "offsets, expected",
    [
        ([], 0),
        ([5], 1),  # одна відмітка — серія довжиною 1
        ([0, 1, 2], 3),
        ([3, 7, 20], 1),  # розкидані одинаки
        ([0, 1, 10, 11, 12, 13, 14], 5),  # найдовша — стара, не свіжа
        ([10, 11, 12, 0, 1], 3),  # порядок у списку не має значення
    ],
)
def test_longest_streak(offsets, expected):
    """Найдовша серія за всю історію."""
    assert stats.longest_streak(set(days_ago(*offsets))) == expected


def test_longest_streak_survives_broken_current():
    """Обірвана поточна серія не стирає рекорд."""
    # П'ять днів поспіль давно, і нічого свіжого.
    result = stats.compute(days_ago(20, 21, 22, 23, 24), TODAY)

    assert result["current_streak"] == 0
    assert result["longest_streak"] == 5
