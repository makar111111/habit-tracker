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
