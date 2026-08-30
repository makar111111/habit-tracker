"""Опис даних: як звичка та відмітка виглядають у базі й у запитах."""

from datetime import date

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


# ---------- Звички ----------

# Спільна основа. Тут поля, які є і в базі, і в тому, що надсилає користувач.
class HabitBase(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""


# table=True перетворює клас на таблицю в базі даних.
class Habit(HabitBase, table=True):
    id: int | None = Field(default=None, primary_key=True)


class HabitCreate(HabitBase):
    pass


# Усі поля необов'язкові: можна змінити лише назву, не чіпаючи опис.
class HabitUpdate(SQLModel):
    name: str | None = None
    description: str | None = None


# ---------- Відмітки виконання ----------


class Checkin(SQLModel, table=True):
    """Одна відмітка: «звичку X зроблено в день Y»."""

    # Правило на рівні бази: пара (звичка, день) не може повторюватись.
    # Тобто відмітити ту саму звичку двічі за один день фізично неможливо —
    # база сама відхилить такий запис, навіть якщо в коді буде помилка.
    __table_args__ = (UniqueConstraint("habit_id", "day"),)

    id: int | None = Field(default=None, primary_key=True)

    # foreign_key="habit.id" — це і є зв'язок. Значення в цій колонці
    # має відповідати справжньому id у таблиці habit.
    # index=True каже базі побудувати покажчик: пошук "усі відмітки звички №2"
    # працюватиме швидко навіть на десятках тисяч записів.
    habit_id: int = Field(foreign_key="habit.id", index=True)

    # Тип date, а не рядок. Тоді база вміє порівнювати й сортувати дати,
    # а FastAPI сам перевірить формат і відхилить "31 лютого".
    day: date


class CheckinCreate(SQLModel):
    """Тіло запиту при створенні відмітки. День необов'язковий."""

    # None означає "не вказано" — тоді підставимо сьогодні.
    # Так можна відмічати заднім числом, якщо забув учора.
    day: date | None = None


# ---------- Статистика ----------


class HabitStats(SQLModel):
    """Відповідь ендпоінта статистики. Це не таблиця — нічого не зберігаємо,
    показники рахуються заново на кожен запит."""

    habit_id: int
    total: int              # скільки всього днів відмічено
    current_streak: int     # днів поспіль просто зараз
    longest_streak: int     # найдовша серія за всю історію
    done_today: bool        # чи відмічено сьогодні
    last_day: date | None   # остання відмітка
