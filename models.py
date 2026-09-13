"""Опис даних: як користувач, звичка та відмітка виглядають у базі й у запитах."""

from datetime import date, datetime

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field as PydanticField, StrictInt, field_validator
from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel

Weekdays = Annotated[list[StrictInt], PydanticField(min_length=1, max_length=7)]


def daily_weekdays() -> list[int]:
    return list(range(7))


def normalize_name(value):
    return value.strip() if isinstance(value, str) else value


# ---------- Користувачі ----------


class User(SQLModel, table=True):
    """Власник звичок.

    З'явився разом із ботом. Поки трекер жив тільки у браузері,
    користувач був один і мався на увазі — тепер до тих самих даних
    приходять різні люди з Telegram, і кожному треба показувати своє.
    """

    id: int | None = Field(default=None, primary_key=True)

    # Хто це в Telegram. unique — щоб та сама людина не завела собі
    # два записи, index — щоб пошук за цим полем був миттєвий
    # (а він відбувається на КОЖЕН запит від бота).
    #
    # None означає "це не з Telegram, а локальний користувач браузера".
    # Такий запис у базі рівно один; SQLite не вважає кілька NULL
    # порушенням unique, тож обмеження цьому не заважає.
    telegram_id: int | None = Field(default=None, unique=True, index=True)

    # Ім'я з Telegram — щоб у логах було видно людину, а не голий номер.
    name: str = ""
    timezone: str = "Europe/Kyiv"
    reminder_hour: int = 20
    reminders_enabled: bool = True
    last_reminder_day: date | None = None


class UserPublic(SQLModel):
    """Відповідь на «а хто я?». Показуємо людині її власні дані."""

    id: int
    telegram_id: int | None
    name: str
    timezone: str
    reminder_hour: int
    reminders_enabled: bool


class LoginToken(SQLModel, table=True):
    """Одноразовий код, яким браузер доводить, що за ним та сама людина,
    що й у Telegram.

    Навіщо взагалі: у браузера немає жодного способу дізнатися, хто його
    власник у Telegram. Тому браузер просить у сервера випадковий код,
    показує посилання t.me/бот?start=КОД, а людина відкриває його вже
    у своєму Telegram — і бот, який достеменно знає, хто до нього
    звернувся, підтверджує цей код. Далі браузер обмінює код на сесію.

    Код — це, по суті, пароль на п'ять хвилин: хто його знає, той і
    отримає сесію. Звідси три правила, що діють нижче й у main.py:
    він випадковий (не вгадаєш), короткоживучий (created_at + TTL)
    і одноразовий (після обміну запис видаляється).
    """

    # Сам код і є первинним ключем: шукати завжди будемо саме за ним.
    token: str = Field(primary_key=True)

    created_at: datetime = Field(default_factory=datetime.now)

    # Порожнє, поки бот не підтвердив. Щойно тут з'явиться id —
    # браузер зможе обміняти код на сесію.
    user_id: int | None = Field(default=None, foreign_key="user.id")


class UserUpdate(SQLModel):
    """Тіло запиту зі зміною імені.

    Ім'я їде саме тілом, а не заголовком: тіло — це JSON у кодуванні UTF-8,
    де «Олена» чи «Ігор» цілком законні. HTTP-заголовки ж за стандартом
    однобайтові, і кирилиця в них ламається.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = None
    reminder_hour: Annotated[StrictInt, PydanticField(ge=0, le=23)] | None = None
    reminders_enabled: bool | None = None

    @field_validator('name', 'timezone', 'reminder_hour', 'reminders_enabled', mode='before')
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError('Значення не може бути null')
        return value

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return normalize_name(value)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError('Невідомий часовий пояс') from None
        return value


# ---------- Звички ----------

# Спільна основа. Тут поля, які є і в базі, і в тому, що надсилає користувач.
class HabitBase(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return normalize_name(value)


# table=True перетворює клас на таблицю в базі даних.
class Habit(HabitBase, table=True):
    id: int | None = Field(default=None, primary_key=True)

    # Власник. Саме це поле перетворює трекер з однокористувацького
    # на багатокористувацький: кожен запит тепер фільтрується по ньому.
    user_id: int = Field(foreign_key="user.id", index=True)
    start_date: date | None = None
    weekdays: list[int] = Field(default_factory=daily_weekdays, sa_column=Column(JSON, nullable=False))
    archived_at: date | None = None


class HabitCreate(HabitBase):
    start_date: date | None = None
    weekdays: Weekdays = Field(default_factory=daily_weekdays)

    @field_validator('weekdays')
    @classmethod
    def valid_weekdays(cls, value):
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError('Обери різні дні тижня від 0 до 6')
        return sorted(value)


class HabitPublic(HabitBase):
    """Те, що API віддає назовні.

    Навмисно НЕ те саме, що Habit: тут немає user_id. Внутрішні номери
    користувачів клієнта не стосуються, а зайве поле у відповіді —
    це підказка сторонньому, скільки в системі людей і хто ти серед них.
    Правило: модель бази і модель відповіді — різні речі.
    """

    id: int
    start_date: date | None
    weekdays: list[int]
    archived_at: date | None


# Усі поля необов'язкові: можна змінити лише назву, не чіпаючи опис.
class HabitUpdate(SQLModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    archived: bool | None = None

    @field_validator('name', 'description', 'archived', mode='before')
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError('Значення не може бути null')
        return value

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return normalize_name(value)


class ReminderSent(SQLModel):
    day: date


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
