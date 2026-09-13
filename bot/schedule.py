"""Розклад звички з відповіді HTTP API, без доступу бота до бази."""

from datetime import date


def is_planned_on(habit: dict, day: date) -> bool:
    """Чи запланована активна звичка на день у часовому поясі власника."""
    if habit.get("archived_at") is not None:
        return False
    start = habit.get("start_date")
    if start and day < date.fromisoformat(start):
        return False
    return day.weekday() in habit.get("weekdays", range(7))


WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
EVERY_DAY = list(range(7))
WORKDAYS = list(range(5))


def schedule_label(weekdays: list[int]) -> str:
    """«Щодня» або «Пн, Ср, Пт» — той самий підпис, що й у вебі (scheduleLabel)."""
    days = sorted(set(weekdays))
    if len(days) == 7:
        return "Щодня"
    return ", ".join(WEEKDAY_NAMES[day] for day in days)


def toggle_day(weekdays: list[int], day: int) -> list[int]:
    """Увімкнути або вимкнути день. Відсортовано, бо API вимагає впорядкований список."""
    days = set(weekdays)
    days.symmetric_difference_update({day})
    return sorted(days)
