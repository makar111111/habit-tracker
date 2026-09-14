"""Тести календарних правил: локальний день і пошук запланованого дня.

До цього calendar_rules перевірявся лише побічно — інші тести підміняли
now_utc, а межі previous_scheduled (inclusive, тиждень, початок календаря)
ніхто не чіпав. Бази й HTTP тут немає, тож тести миттєві.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import calendar_rules
from calendar_rules import previous_scheduled

MONDAY = date(2026, 9, 14)  # weekday() == 0
MON, TUE, SUN = 0, 1, 6


def test_local_day_depends_on_timezone(monkeypatch):
    """Та сама мить у UTC — різні календарні дні в Києві й Нью-Йорку."""
    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc),
    )

    assert calendar_rules.local_day("Europe/Kyiv") == date(2026, 9, 13)
    assert calendar_rules.local_day("America/New_York") == date(2026, 9, 12)


def test_now_utc_is_timezone_aware():
    """Наївний datetime зламав би astimezone у local_day мовчки — з локальним поясом сервера."""
    assert calendar_rules.now_utc().tzinfo is not None


def test_no_weekdays_means_nothing_scheduled():
    assert previous_scheduled(MONDAY, set()) is None
    assert previous_scheduled(MONDAY, set(), inclusive=True) is None


def test_exclusive_skips_the_day_itself():
    """Без inclusive сьогоднішній день не рахується, навіть якщо він запланований."""
    assert previous_scheduled(MONDAY, {MON}) == date(2026, 9, 7)


def test_inclusive_returns_the_day_itself():
    assert previous_scheduled(MONDAY, {MON}, inclusive=True) == MONDAY


def test_finds_yesterday():
    assert previous_scheduled(MONDAY, {SUN}) == date(2026, 9, 13)


def test_picks_the_nearest_of_several_weekdays():
    assert previous_scheduled(MONDAY, {TUE, SUN}) == date(2026, 9, 13)


def test_inclusive_reaches_six_days_back():
    """inclusive шукає на 0..6 днів назад — рівно тиждень, включно з сьогодні."""
    assert previous_scheduled(MONDAY, {TUE}, inclusive=True) == date(2026, 9, 8)


@pytest.mark.parametrize("weekday", range(7))
@pytest.mark.parametrize("inclusive", [False, True])
def test_any_single_weekday_is_found_within_a_week(weekday, inclusive):
    """Для будь-якого дня тижня результат є, має потрібний weekday і лежить у межах тижня."""
    found = previous_scheduled(MONDAY, {weekday}, inclusive=inclusive)

    assert found is not None
    assert found.weekday() == weekday
    back = (MONDAY - found).days
    assert (0 <= back <= 6) if inclusive else (1 <= back <= 7)


def test_start_of_calendar_returns_none_instead_of_overflow():
    """date.min - 1 день кидає OverflowError; функція має чесно сказати «немає»."""
    assert previous_scheduled(date.min, {MON}) is None
    assert previous_scheduled(date.min, {TUE}, inclusive=True) is None


def test_start_of_calendar_still_finds_reachable_days():
    """Поруч із date.min дні, що існують, знаходяться як завжди."""
    assert date.min.weekday() == MON
    assert previous_scheduled(date.min, {MON}, inclusive=True) == date.min
    assert previous_scheduled(date.min + timedelta(days=2), {MON}) == date.min
