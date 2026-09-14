"""Спільний годинник користувача й календар запланованих виконань."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_day(timezone_name: str) -> date:
    return now_utc().astimezone(ZoneInfo(timezone_name)).date()


def previous_scheduled(
    day: date, weekdays: set[int], *, inclusive: bool = False
) -> date | None:
    """Найближчий запланований день; пошук обмежений одним тижнем."""
    if not weekdays:
        return None
    for offset in range(0 if inclusive else 1, 7 if inclusive else 8):
        if day.toordinal() <= offset:
            return None
        candidate = day - timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate
    return None
