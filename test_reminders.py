"""Тести щоденних нагадувань.

Чисту логіку (час, текст, число) перевіряємо без жодного очікування чи
мережі — це прямі виклики функцій. Розсилку — проти справжнього API
через ASGITransport, як і решту тестів бота.
"""

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

import auth
from bot.api import HabitsAPI
from bot.reminders import (
    next_run_at,
    pluralize_habits,
    reminder_text,
    send_reminders,
    undone_habit_names,
)
from conftest import TEST_BOT_SECRET
from database import get_session
from main import app

OLENA = 111
IHOR = 222


# ---------- next_run_at: чиста функція, ніякого реального сну ----------


def test_next_run_at_later_today():
    now = datetime(2026, 8, 31, 12, 0)
    assert next_run_at(now, hour=20) == datetime(2026, 8, 31, 20, 0)


def test_next_run_at_already_passed_today():
    now = datetime(2026, 8, 31, 21, 0)
    assert next_run_at(now, hour=20) == datetime(2026, 9, 1, 20, 0)


def test_next_run_at_exactly_now_waits_for_tomorrow():
    """Годинник рівно 20:00 — чекаємо ЗАВТРАШНІХ 20:00, а не 0 секунд.

    Інакше цикл спрацював би двічі за одну мить рівно на межі години:
    один раз "негайно" (0 секунд очікування), другий — за розкладом.
    """
    now = datetime(2026, 8, 31, 20, 0)
    assert next_run_at(now, hour=20) == datetime(2026, 9, 1, 20, 0)


def test_next_run_at_respects_custom_hour():
    now = datetime(2026, 8, 31, 8, 0)
    assert next_run_at(now, hour=9) == datetime(2026, 8, 31, 9, 0)


# ---------- pluralize_habits: українська форма числівника ----------


@pytest.mark.parametrize(
    "count,expected",
    [
        (1, "звичку"),
        (2, "звички"),
        (3, "звички"),
        (4, "звички"),
        (5, "звичок"),
        (10, "звичок"),
        (11, "звичок"),
        (12, "звичок"),
        (14, "звичок"),
        (21, "звичку"),
        (22, "звички"),
        (25, "звичок"),
    ],
)
def test_pluralize_habits(count, expected):
    assert pluralize_habits(count) == expected


# ---------- reminder_text ----------


def test_reminder_text_lists_all_names():
    text = reminder_text(["Йога", "Читати"])

    assert "Йога" in text
    assert "Читати" in text
    assert "2" in text
    assert "звички" in text


# ---------- undone_habit_names + send_reminders: проти справжнього API ----------


@pytest.fixture(name="api")
async def api_fixture(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Той самий патерн, що в test_bot_api.py: база у файлі, сесія на
    кожен запит — потрібно, бо habits_with_stats ходить у два ендпоінти
    одночасно через asyncio.gather (детальніше — коментар там)."""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'reminders.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    monkeypatch.setattr(auth, "BOT_SECRET", TEST_BOT_SECRET)

    api = HabitsAPI(secret=TEST_BOT_SECRET)
    await api._client.aclose()
    api._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    yield api

    await api.close()
    app.dependency_overrides.clear()
    engine.dispose()


# Окремого pytestmark = pytest.mark.asyncio тут НЕ ставимо: asyncio_mode
# = auto в pytest.ini сам розпізнає async-тести за сигнатурою. А цей
# файл має й синхронні тести вище (next_run_at, pluralize_habits) —
# module-level pytestmark позначив би і їх, і pytest сварився б
# попередженням на кожен такий тест.


async def test_undone_habit_names_filters_out_done(api: HabitsAPI):
    h1 = await api.create_habit(OLENA, "Йога")
    await api.create_habit(OLENA, "Читати")
    await api.check_in(OLENA, h1["id"])

    assert await undone_habit_names(api, OLENA) == ["Читати"]


async def test_undone_habit_names_empty_when_all_done(api: HabitsAPI):
    h = await api.create_habit(OLENA, "Йога")
    await api.check_in(OLENA, h["id"])

    assert await undone_habit_names(api, OLENA) == []


async def test_list_telegram_users_excludes_local_browser_user(api: HabitsAPI):
    """Локальний користувач браузера (без telegram_id) не має потрапляти
    у список для розсилки — надсилати йому нагадування нікуди."""
    await api.create_habit(OLENA, "Йога")
    await api._client.get("/users/me")  # без заголовків -> локальний користувач

    assert await api.list_telegram_users() == [OLENA]


class FakeBot:
    """Замість справжнього aiogram.Bot — записує виклики send_message.

    send_reminders потребує лише один метод, тож повноцінний Bot
    (із токеном і сесією) тут зайвий.
    """

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.fail_for: set[int] = set()

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self.fail_for:
            # Найчастіша причина в реальності — людина заблокувала бота.
            raise RuntimeError("simulated: заблокував бота")
        self.sent.append((chat_id, text))


async def test_send_reminders_only_to_users_with_undone_habits(api: HabitsAPI):
    h1 = await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")
    await api.check_in(OLENA, h1["id"])  # Олена все відмітила, Ігор — ні

    bot = FakeBot()
    await send_reminders(bot, api)

    assert [chat_id for chat_id, _ in bot.sent] == [IHOR]


async def test_send_reminders_skips_when_no_telegram_users(api: HabitsAPI):
    await api._client.get("/users/me")  # тільки локальний, telegram-юзерів немає

    bot = FakeBot()
    await send_reminders(bot, api)

    assert bot.sent == []


async def test_send_reminders_one_blocked_user_does_not_stop_others(api: HabitsAPI):
    """Заблокований бот у однієї людини не має рвати розсилку решті."""
    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")

    bot = FakeBot()
    bot.fail_for = {OLENA}

    await send_reminders(bot, api)

    assert [chat_id for chat_id, _ in bot.sent] == [IHOR]


async def test_send_reminders_text_names_the_undone_habit(api: HabitsAPI):
    await api.create_habit(OLENA, "Пити воду")

    bot = FakeBot()
    await send_reminders(bot, api)

    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == OLENA
    assert "Пити воду" in text
