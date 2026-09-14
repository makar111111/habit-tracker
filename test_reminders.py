"""Тести щоденних нагадувань.

Чисту логіку (час, текст, число) перевіряємо без жодного очікування чи
мережі — це прямі виклики функцій. Розсилку — проти справжнього API
через ASGITransport, як і решту тестів бота.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

import auth
import calendar_rules
from bot import reminders
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
NOW = datetime(2026, 9, 12, 17, tzinfo=timezone.utc)


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


@pytest.mark.parametrize(
    "zone,hour,last_day,now,expected",
    [
        ("Europe/Kyiv", 20, None, NOW, date(2026, 9, 12)),
        ("America/New_York", 20, None, NOW, None),
        ("Europe/Kyiv", 19, None, NOW, date(2026, 9, 12)),
        ("Europe/Kyiv", 20, "2026-09-12", NOW, None),
        ("Europe/Kyiv", 20, "2026-09-13", NOW, None),
        ("Europe/Kyiv", 20, "2026-09-11", NOW, date(2026, 9, 12)),
        (
            "America/New_York",
            20,
            None,
            datetime(2026, 9, 12, 0, tzinfo=timezone.utc),
            date(2026, 9, 11),
        ),
        # Перехід на літній час пропустив 03:00: нагадуємо о 04:00.
        (
            "Europe/Kyiv",
            3,
            None,
            datetime(2026, 3, 29, 1, tzinfo=timezone.utc),
            date(2026, 3, 29),
        ),
        # Повторена осіння година не надсилає друге нагадування.
        (
            "Europe/Kyiv",
            3,
            "2026-10-25",
            datetime(2026, 10, 25, 1, tzinfo=timezone.utc),
            None,
        ),
    ],
)
def test_reminder_day_uses_personal_schedule(zone, hour, last_day, now, expected):
    target = {
        "telegram_id": OLENA,
        "timezone": zone,
        "reminder_hour": hour,
        "last_reminder_day": last_day,
    }

    assert reminders.reminder_day(target, now) == expected


def test_reminder_day_rejects_naive_clock():
    target = {
        "telegram_id": OLENA,
        "timezone": "Europe/Kyiv",
        "reminder_hour": 20,
        "last_reminder_day": None,
    }

    with pytest.raises(ValueError, match="timezone|пояс"):
        reminders.reminder_day(target, datetime(2026, 9, 12, 20))


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({}, True),
        ({"start_date": None, "weekdays": list(range(7)), "archived_at": None}, True),
        ({"start_date": "2026-09-12", "weekdays": [5]}, True),
        ({"start_date": "2026-09-13"}, False),
        ({"weekdays": [0, 2, 4]}, False),
        ({"weekdays": []}, False),
        ({"archived_at": "2026-09-12"}, False),
    ],
)
def test_planned_habit_respects_start_weekdays_and_archive(fields, expected):
    from bot import schedule

    assert schedule.is_planned_on(fields, date(2026, 9, 12)) is expected


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


def test_reminder_text_escapes_html_in_habit_names():
    text = reminder_text(["Читати <книгу> & <b>писати</b>"])

    assert "Читати &lt;книгу&gt; &amp; &lt;b&gt;писати&lt;/b&gt;" in text
    assert "<b>" not in text


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
    monkeypatch.setattr(auth, "ALLOW_LOCAL_USER", True)
    monkeypatch.setattr(calendar_rules, "now_utc", lambda: NOW)

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
    await send_reminders(bot, api, now=NOW)

    assert [chat_id for chat_id, _ in bot.sent] == [IHOR]


async def test_send_reminders_skips_when_no_telegram_users(api: HabitsAPI):
    await api._client.get("/users/me")  # тільки локальний, telegram-юзерів немає

    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert bot.sent == []


async def test_send_reminders_one_blocked_user_does_not_stop_others(api: HabitsAPI):
    """Заблокований бот у однієї людини не має рвати розсилку решті."""
    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")

    bot = FakeBot()
    bot.fail_for = {OLENA}

    await send_reminders(bot, api, now=NOW)

    assert [chat_id for chat_id, _ in bot.sent] == [IHOR]


async def test_send_reminders_text_names_the_undone_habit(api: HabitsAPI):
    await api.create_habit(OLENA, "Пити воду")

    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == OLENA
    assert "Пити воду" in text


async def test_send_reminders_respects_personal_hour_and_timezone(api: HabitsAPI):
    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")
    response = await api._request(
        "PATCH",
        "/users/me",
        IHOR,
        json={"timezone": "America/New_York", "reminder_hour": 20},
    )
    assert response.status_code == 200

    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert [chat_id for chat_id, _ in bot.sent] == [OLENA]


async def test_send_reminders_excludes_disabled_users(api: HabitsAPI):
    await api.create_habit(OLENA, "Йога")
    response = await api._request(
        "PATCH", "/users/me", OLENA, json={"reminders_enabled": False}
    )
    assert response.status_code == 200

    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert bot.sent == []
    assert await api.list_reminder_targets() == []


async def test_reminder_lists_only_habits_planned_for_local_day(api: HabitsAPI):
    await api.create_habit(OLENA, "Щодня")
    for name, fields in [
        ("За розкладом", {"weekdays": [5]}),
        ("Відпочинок", {"weekdays": [0, 2, 4]}),
        ("Архів", {}),
    ]:
        response = await api._request(
            "POST", "/habits", OLENA, json={"name": name, **fields}
        )
        assert response.status_code == 201, response.text
        if name == "Архів":
            response = await api._request(
                "PATCH",
                f"/habits/{response.json()['id']}",
                OLENA,
                json={"archived": True},
            )
            assert response.status_code == 200, response.text

    assert await undone_habit_names(api, OLENA) == ["Щодня", "За розкладом"]
    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)
    assert len(bot.sent) == 1
    assert "Щодня" in bot.sent[0][1]
    assert "За розкладом" in bot.sent[0][1]
    for excluded in ["Відпочинок", "Архів"]:
        assert excluded not in bot.sent[0][1]


async def test_rest_day_does_not_consume_reminder_day(api: HabitsAPI):
    response = await api._request(
        "POST", "/habits", OLENA, json={"name": "Йога", "weekdays": [0]}
    )
    assert response.status_code == 201, response.text

    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert bot.sent == []
    assert (await api.list_reminder_targets())[0]["last_reminder_day"] is None


async def test_sent_reminder_survives_restart_and_next_day_is_due(
    api: HabitsAPI, monkeypatch
):
    await api.create_habit(OLENA, "Йога")
    first_bot = FakeBot()
    await send_reminders(first_bot, api, now=NOW)
    assert len(first_bot.sent) == 1

    restarted_bot = FakeBot()
    await send_reminders(restarted_bot, api, now=NOW + timedelta(minutes=1))
    assert restarted_bot.sent == []

    tomorrow = NOW + timedelta(days=1)
    monkeypatch.setattr(calendar_rules, "now_utc", lambda: tomorrow)
    await send_reminders(restarted_bot, api, now=tomorrow)
    assert len(restarted_bot.sent) == 1
    assert (await api.list_reminder_targets())[0]["last_reminder_day"] == "2026-09-13"


async def test_timezone_change_to_previous_day_does_not_repeat_reminder(
    api: HabitsAPI, monkeypatch
):
    now = datetime(2026, 9, 12, 23, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar_rules, "now_utc", lambda: now)
    response = await api._request(
        "POST", "/habits", OLENA, json={"name": "Йога", "start_date": "2026-09-01"}
    )
    assert response.status_code == 201, response.text
    response = await api._request(
        "PATCH", "/users/me", OLENA, json={"reminder_hour": 0}
    )
    assert response.status_code == 200, response.text

    first_bot = FakeBot()
    await send_reminders(first_bot, api, now=now)
    assert len(first_bot.sent) == 1
    assert (await api.list_reminder_targets())[0]["last_reminder_day"] == "2026-09-13"

    response = await api._request(
        "PATCH", "/users/me", OLENA, json={"timezone": "America/New_York"}
    )
    assert response.status_code == 200, response.text
    assert await api.today(OLENA) == date(2026, 9, 12)
    assert await undone_habit_names(api, OLENA) == ["Йога"]
    restarted_bot = FakeBot()
    await send_reminders(restarted_bot, api, now=now)

    assert restarted_bot.sent == []


async def test_failed_delivery_is_retried_without_ack(api: HabitsAPI):
    await api.create_habit(OLENA, "Йога")
    bot = FakeBot()
    bot.fail_for = {OLENA}

    await send_reminders(bot, api, now=NOW)
    assert (await api.list_reminder_targets())[0]["last_reminder_day"] is None

    bot.fail_for.clear()
    await send_reminders(bot, api, now=NOW + timedelta(minutes=1))
    assert len(bot.sent) == 1
    assert (await api.list_reminder_targets())[0]["last_reminder_day"] == "2026-09-12"


async def test_ack_failure_is_logged_and_does_not_stop_others(
    api: HabitsAPI, monkeypatch, caplog
):
    from bot.api import ApiUnavailable

    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")
    acknowledge = api.mark_reminder_sent

    async def fail_for_olena(telegram_id, day):
        if telegram_id == OLENA:
            raise ApiUnavailable("З'єднання перервано після доставки")
        await acknowledge(telegram_id, day)

    monkeypatch.setattr(api, "mark_reminder_sent", fail_for_olena)
    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert [chat_id for chat_id, _ in bot.sent] == [OLENA, IHOR]
    targets = {t["telegram_id"]: t for t in await api.list_reminder_targets()}
    assert targets[OLENA]["last_reminder_day"] is None
    assert targets[IHOR]["last_reminder_day"] == "2026-09-12"
    assert "повтор" in caplog.text.lower()


async def test_one_user_api_failure_does_not_stop_reminders(
    api: HabitsAPI, monkeypatch
):
    from bot.api import ApiUnavailable

    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")
    habits_with_stats = api.habits_with_stats

    async def fail_for_olena(telegram_id):
        if telegram_id == OLENA:
            raise ApiUnavailable("Тимчасовий збій")
        return await habits_with_stats(telegram_id)

    monkeypatch.setattr(api, "habits_with_stats", fail_for_olena)
    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)

    assert [chat_id for chat_id, _ in bot.sent] == [IHOR]


async def test_target_list_unavailable_leaves_no_delivery(api: HabitsAPI, monkeypatch):
    from bot.api import ApiUnavailable

    async def fail():
        raise ApiUnavailable("Тимчасовий збій")

    monkeypatch.setattr(api, "list_reminder_targets", fail)
    bot = FakeBot()
    await send_reminders(bot, api, now=NOW)
    assert bot.sent == []


async def test_reminder_loop_runs_every_minute_and_survives_failure(monkeypatch):
    calls = []

    async def send(*args, **kwargs):
        calls.append("send")
        if len(calls) == 1:
            raise RuntimeError("Тимчасовий збій")

    async def sleep(seconds):
        calls.append(seconds)
        if len(calls) >= 4:
            raise asyncio.CancelledError

    monkeypatch.setattr(reminders, "send_reminders", send)
    monkeypatch.setattr(reminders.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await reminders.reminder_loop(FakeBot(), object())

    assert calls == ["send", 60, "send", 60]
