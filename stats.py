"""Обчислення статистики за списком днів.

Тут навмисно немає ні бази даних, ні FastAPI — лише робота з датами.
Завдяки цьому функцію можна перевірити, просто передавши їй список дат.
"""

from datetime import date, timedelta

from calendar_rules import previous_scheduled

ONE_DAY = timedelta(days=1)


def longest_streak(days: set[date]) -> int:
    """Найдовша серія днів поспіль за всю історію."""
    best = 0
    for day in days:
        # Рахуємо серію лише від її початку — тобто від дня,
        # перед яким немає відмітки. Інакше та сама серія
        # перераховувалась би стільки разів, скільки в ній днів.
        if day - ONE_DAY in days:
            continue

        length = 1
        while day + length * ONE_DAY in days:
            length += 1

        best = max(best, length)

    return best


def current_streak(days: set[date], today: date) -> int:
    """Скільки днів поспіль звичку виконують просто зараз.

    Серія вважається живою, якщо відмічено сьогодні АБО вчора.
    Вчора — навмисно: якщо ще ранок і ти сьогодні не встиг,
    несправедливо було б обнуляти двадцятиденну серію.
    """
    if today in days:
        cursor = today
    elif today - ONE_DAY in days:
        cursor = today - ONE_DAY
    else:
        return 0

    length = 0
    while cursor in days:
        length += 1
        cursor -= ONE_DAY

    return length


def compute(
    days: list[date], today: date, *, weekdays: list[int] | None = None,
    start_date: date | None = None, archived_at: date | None = None,
) -> dict:
    """Зібрати всі показники разом."""
    # set замість list з двох причин: прибирає можливі дублікати
    # і робить перевірку "чи є такий день" миттєвою.
    unique = set(days)

    scheduled_weekdays = set(range(7) if weekdays is None else weekdays)
    end = min(today, archived_at) if archived_at else today
    completed = {
        day for day in unique
        if day <= end and (start_date is None or day >= start_date)
        and day.weekday() in scheduled_weekdays
    }
    runs: dict[date, int] = {}
    for day in sorted(completed):
        previous = previous_scheduled(day, scheduled_weekdays)
        runs[day] = runs.get(previous, 0) + 1

    anchor = previous_scheduled(end, scheduled_weekdays, inclusive=True)
    # Сьогодні ще можна встигнути. Пропущений минулий запланований день
    # уже обриває серію, навіть якщо сьогодні вихідний.
    if anchor == today and anchor not in completed:
        anchor = previous_scheduled(anchor, scheduled_weekdays)

    return {
        "total": len(unique),
        "current_streak": runs.get(anchor, 0),
        "longest_streak": max(runs.values(), default=0),
        "done_today": today in unique,
        "last_day": max(unique) if unique else None,
    }
