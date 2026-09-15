"""Тести українського відмінювання числівників (bot/plural.py).

Досі plural перевірявся лише побічно — через pluralize_habits у
test_reminders.py (1-25, слово «звичка»). Тут — сама функція напряму:
сотні (111-114 і 101/121), великі числа й від'ємні. Бази й HTTP немає,
тож тести миттєві.
"""

import pytest

from bot.plural import plural

DAYS = ("день", "дні", "днів")  # так рахує картка звички в bot/views.py
HABITS = ("звичку", "звички", "звичок")  # так рахують нагадування


def days(count: int) -> str:
    return plural(count, *DAYS)


@pytest.mark.parametrize("count", [1, 21, 31, 101, 121, 1001, 1_000_001])
def test_ends_with_one_takes_form_one(count):
    """Остання цифра 1 (крім 11 у кінці) — «день»: «21 день», «101 день»."""
    assert days(count) == "день"


@pytest.mark.parametrize("count", [2, 3, 4, 22, 23, 24, 102, 1004, 1_000_002])
def test_ends_with_two_to_four_takes_form_few(count):
    assert days(count) == "дні"


@pytest.mark.parametrize("count", [0, *range(5, 21), 25, 30, 100, 1000, 1_000_000])
def test_zero_and_five_to_twenty_take_form_many(count):
    """0 теж «днів»: «0 днів», а не «0 день» — остання цифра 0 потрапляє в many."""
    assert days(count) == "днів"


@pytest.mark.parametrize("count", [11, 12, 13, 14, 111, 112, 113, 114, 1011, 1_000_012])
def test_eleven_to_fourteen_are_exception(count):
    """11-14 закінчуються на 1-4, але беруть many: «11 днів», не «11 день».

    Виняток дивиться на ДВІ останні цифри (count % 100), тому діє й
    для 111-114, 1011 тощо. Перевірка лише 11-14 не відрізнила б
    правильний код від помилкового «count == 11».
    """
    assert days(count) == "днів"


@pytest.mark.parametrize(
    "count,expected",
    [(1, "звичку"), (3, "звички"), (11, "звичок"), (21, "звичку"), (112, "звичок")],
)
def test_other_words_follow_the_same_rule(count, expected):
    """Функція не знає слів — лише обирає аргумент за позицією."""
    assert plural(count, *HABITS) == expected


@pytest.mark.parametrize(
    "count,expected",
    [
        (-1, "днів"),
        (-2, "днів"),
        (-5, "днів"),
        (-8, "дні"),
        (-9, "день"),
        (-11, "днів"),
        (-21, "днів"),
    ],
)
def test_negative_numbers_actual_behaviour(count, expected):
    """ФІКСУЄ ФАКТ, а не бажану поведінку.

    Python рахує % для від'ємних до невід'ємного залишку: -1 % 10 == 9,
    -9 % 10 == 1. Тому «-1 днів», але «-9 день» і «-8 дні» — граматично
    неправильно (очікувалося б як для модуля числа). У бойових викликах
    лічильники (серія, усього, кількість звичок) від'ємними не бувають,
    тож це не баг сьогодні, а пастка на майбутнє. Якщо поведінку
    виправлять (abs(count)), цей тест треба оновити свідомо.
    """
    assert days(count) == expected
