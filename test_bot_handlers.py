"""Тести самих обробників бота — з прогоном оновлень через диспетчер.

Чим відрізняється від test_bot_api.py: там перевіряється клієнт до API,
тут — те, що бот РОБИТЬ у відповідь на дії людини. Виконуються справжні
роутери, фільтри, стани й порядок обробників.

Мережі немає з обох боків:
  • до Telegram — підставлена сесія, яка не шле нічого, а записує виклики;
  • до API — ASGITransport, тобто застосунок викликається напряму.

Ці тести зʼявилися після того, як ручний прогін знайшов дві помилки,
яких не бачив жоден тест клієнта: команда посеред діалогу ставала назвою
звички, а стара кнопка вішала «годинник» назавжди. Обидві — нижче.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    AnswerPreCheckoutQuery,
    EditMessageText,
    SendInvoice,
    SendMessage,
)
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

import auth
import calendar_rules
from bot import checkin, manage, menu, new_habit, settings, support
from bot.__main__ import on_error
from bot.api import HabitsAPI
from conftest import TEST_BOT_SECRET
from database import get_session
from main import app

# Окремого маркування async-тестів не треба: asyncio_mode = auto
# у pytest.ini сам розпізнає їх за сигнатурою. pytestmark = asyncio
# тут навмисно НЕ ставимо — це позначило б і два синхронні тести
# наприкінці файлу (check_settings, shorten), і pytest-asyncio на це
# сварився б попередженням.

USER = User(id=4242, is_bot=False, first_name="Олена", last_name="К")
CHAT = Chat(id=4242, type="private")


class RecordingSession(BaseSession):
    """Сесія, яка нічого не надсилає, а складає виклики у список.

    Так тест бачить рівно те, що побачив би користувач: текст
    повідомлень, підписи кнопок і спливаючі підказки.
    """

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, list[str]]] = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, (SendMessage, EditMessageText)):
            markup = getattr(method, "reply_markup", None)
            buttons = (
                [b.text for row in markup.inline_keyboard for b in row]
                if markup
                else []
            )
            self.sent.append((type(method).__name__, method.text, buttons))
            return Message(
                message_id=len(self.sent),
                date=datetime.now(),
                chat=CHAT,
                text=method.text,
            )

        if isinstance(method, AnswerCallbackQuery):
            # Найважливіший запис у цьому файлі. Поки бот не відповість
            # на натискання, Telegram крутить на кнопці «годинник».
            self.sent.append(("Alert", method.text or "", []))
            return True

        if isinstance(method, SendInvoice):
            # Замість кнопок — ціни: саме їх людина побачить у рахунку.
            prices = [f"{p.amount} {method.currency}" for p in method.prices]
            self.sent.append(("SendInvoice", method.payload, prices))
            return Message(message_id=len(self.sent), date=datetime.now(), chat=CHAT)

        if isinstance(method, AnswerPreCheckoutQuery):
            verdict = "ok" if method.ok else "reject"
            self.sent.append(("PreCheckout", method.error_message or "", [verdict]))
            return True

        return True


class BotUnderTest:
    """Бот, диспетчер і записані відповіді — в одному об'єкті."""

    def __init__(self, dispatcher: Dispatcher, bot: Bot, api: HabitsAPI):
        self.dispatcher = dispatcher
        self.bot = bot
        self.api = api
        self._update_id = 0

    @property
    def sent(self) -> list[tuple[str, str, list[str]]]:
        return self.bot.session.sent

    @property
    def texts(self) -> list[str]:
        return [text for _, text, _ in self.sent]

    @property
    def buttons(self) -> list[str]:
        return [b for _, _, row in self.sent for b in row]

    async def send(self, text: str | None) -> None:
        """Людина написала повідомлення. text=None — стікер чи фото."""
        self._update_id += 1
        self.bot.session.sent.clear()
        await self.dispatcher.feed_update(
            self.bot,
            Update(
                update_id=self._update_id,
                message=Message(
                    message_id=self._update_id,
                    date=datetime.now(),
                    chat=CHAT,
                    from_user=USER,
                    text=text,
                ),
            ),
        )

    async def tap(self, data: str) -> None:
        """Людина натиснула інлайн-кнопку."""
        self._update_id += 1
        self.bot.session.sent.clear()
        await self.dispatcher.feed_update(self.bot, self.make_tap(data))

    def make_tap(self, data: str) -> Update:
        """Зібрати оновлення-натискання, не надсилаючи його одразу.

        Потрібно для тестів на одночасність (подвійний тап, подвійний
        клік): там send()/tap() з автоматичним sent.clear() перед
        кожним викликом не годяться — clear() двох майже одночасних
        викликів затер би відповідь одне одному. Тест сам створює
        кілька Update і подає їх у asyncio.gather.
        """
        self._update_id += 1
        return Update(
            update_id=self._update_id,
            callback_query=CallbackQuery(
                id=str(self._update_id),
                from_user=USER,
                chat_instance="test",
                data=data,
                message=Message(
                    message_id=self._update_id,
                    date=datetime.now(),
                    chat=CHAT,
                    text="список",
                ),
            ),
        )

    async def feed(self, update: Update) -> None:
        """Подати заздалегідь зібране оновлення без очищення sent."""
        await self.dispatcher.feed_update(self.bot, update)

    async def photo(self, caption: str | None) -> None:
        """Людина надіслала фото (за бажанням — із підписом)."""
        from aiogram.types import PhotoSize

        self._update_id += 1
        self.bot.session.sent.clear()
        await self.dispatcher.feed_update(
            self.bot,
            Update(
                update_id=self._update_id,
                message=Message(
                    message_id=self._update_id,
                    date=datetime.now(),
                    chat=CHAT,
                    from_user=USER,
                    photo=[
                        PhotoSize(file_id="f", file_unique_id="fu", width=10, height=10)
                    ],
                    caption=caption,
                ),
            ),
        )

    async def fsm_state(self) -> str | None:
        """Поточний стан FSM цього користувача — для перевірок у тестах."""
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(bot_id=self.bot.id, chat_id=CHAT.id, user_id=USER.id)
        return await self.dispatcher.storage.get_state(key=key)


@pytest.fixture(name="tg")
async def tg_fixture(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Повністю зібраний бот із тимчасовою базою.

    Сесія бази створюється на кожен запит — так само, як у бойовому
    get_session. Спільна на всіх сесія тут не годиться: habits_with_stats
    ходить у два ендпоінти одночасно (див. коментар у test_bot_api.py).
    """
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'handlers.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    monkeypatch.setattr(auth, "BOT_SECRET", TEST_BOT_SECRET)

    api = HabitsAPI(secret=TEST_BOT_SECRET)
    await api._client.aclose()
    api._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    bot = Bot(
        token="42:TEST",
        session=RecordingSession(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["api"] = api
    dispatcher.errors.register(on_error)

    # Порядок той самий, що в __main__.py — інакше тести перевіряли б
    # не той бот, який запускається насправді.
    dispatcher.include_router(support.router)
    dispatcher.include_router(settings.router)
    dispatcher.include_router(new_habit.router)
    dispatcher.include_router(manage.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(checkin.router)

    yield BotUnderTest(dispatcher, bot, api)

    await api.close()
    await bot.session.close()
    app.dependency_overrides.clear()
    engine.dispose()

    # Роутери — модульні одинаки: підключений роутер запамʼятовує свого
    # диспетчера й вдруге підключитись відмовляється. У бойовому запуску
    # це правильно (захищає від випадкового подвійного підключення),
    # але кожен тест будує диспетчер заново, тож відвʼязуємо їх назад.
    # Інакше все, крім першого тесту, падало б із
    # "Router is already attached".
    for router in (
        support.router,
        settings.router,
        new_habit.router,
        manage.router,
        menu.router,
        checkin.router,
    ):
        router._parent_router = None


async def create_habit_via_dialog(tg: BotUnderTest, name: str) -> int:
    """Пройти діалог створення до кінця й повернути id звички."""
    await tg.send("/new")
    await tg.send(name)
    await tg.tap("menu:skip_description")
    await tg.tap("sched:done:0")

    habits = await tg.api.list_habits(USER.id)
    return habits[-1]["id"]


# ---------- знайомство ----------


async def test_start_greets_and_shows_empty_list(tg: BotUnderTest):
    await tg.send("/start")

    assert "Вітаю, Олена" in tg.texts[0]
    assert "ще немає жодної звички" in tg.texts[0]
    assert tg.buttons == ["➕ Нова звичка", "⚙️ Керувати"]


async def test_start_saves_name(tg: BotUnderTest):
    await tg.send("/start")

    response = await tg.api._request("GET", "/users/me", USER.id)
    assert response.json()["name"] == "Олена К"


async def test_api_error_message_escapes_html(tg: BotUnderTest, monkeypatch):
    from bot.api import ApiError

    async def fail(*args, **kwargs):
        raise ApiError("Помилка <input> & спробуй ще раз")

    monkeypatch.setattr(tg.api, "list_habits", fail)
    await tg.send("/habits")

    assert tg.texts == ["Помилка &lt;input&gt; &amp; спробуй ще раз"]


# ---------- створення звички ----------


async def test_full_creation_dialog(tg: BotUnderTest):
    await tg.send("/new")
    assert "Як назвемо звичку" in tg.texts[0]

    await tg.send("Зарядка")
    assert "опис" in tg.texts[0]
    assert tg.buttons == ["Пропустити"]

    await tg.tap("menu:skip_description")
    assert "Коли виконувати" in tg.texts[-1]
    assert "Зараз: <b>Щодня</b>" in tg.texts[-1]
    assert tg.buttons[:7] == [
        "✅ Пн",
        "✅ Вт",
        "✅ Ср",
        "✅ Чт",
        "✅ Пт",
        "✅ Сб",
        "✅ Нд",
    ]
    assert "Створити звичку" in tg.buttons
    # Звички ще немає: без розкладу створювати рано.
    assert await tg.api.list_habits(USER.id) == []

    await tg.tap("sched:done:0")
    assert "Готово" in tg.texts[-1]
    assert "⬜ Зарядка" in tg.buttons

    habits = await tg.api.list_habits(USER.id)
    assert [h["name"] for h in habits] == ["Зарядка"]
    assert habits[0]["weekdays"] == [0, 1, 2, 3, 4, 5, 6]


async def test_description_is_saved(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send("Читати")
    await tg.send("20 хвилин перед сном")
    await tg.tap("sched:done:0")

    habits = await tg.api.list_habits(USER.id)
    assert habits[0]["description"] == "20 хвилин перед сном"


async def test_command_inside_dialog_does_not_become_a_name(tg: BotUnderTest):
    """Регресійний тест на справжню помилку.

    Роутер new_habit підключений раніше за menu, а обробник назви
    приймав будь-який текст. Тому /habits посеред діалогу створював
    звичку з назвою «/habits» — людина навіть не розуміла, звідки та
    взялася, бо списку так і не побачила.
    """
    await tg.send("/new")
    await tg.send("/habits")

    assert "Спершу завершимо" in tg.texts[0]

    # Найголовніше: звичка з такою назвою НЕ зʼявилась.
    assert await tg.api.list_habits(USER.id) == []


async def test_cancel_aborts_dialog(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send("/cancel")

    assert "Скасовано" in tg.texts[0]

    # Після скасування текст знову звичайний, а не назва звички.
    await tg.send("просто повідомлення")
    assert await tg.api.list_habits(USER.id) == []


async def test_sticker_instead_of_name_gets_explanation(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send(None)  # повідомлення без тексту

    assert "Потрібен текст" in tg.texts[0]


async def test_too_long_name_is_rejected_kindly(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send("Я" * 150)

    assert "Задовга назва" in tg.texts[0]
    assert await tg.api.list_habits(USER.id) == []


# ---------- відмітки ----------


async def test_tapping_habit_marks_and_unmarks(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Зарядка")

    await tg.tap(f"habit:toggle:{habit_id}")
    assert "Відмічено" in tg.texts[0]
    assert any("✅ Зарядка" in b for b in tg.buttons)

    await tg.tap(f"habit:toggle:{habit_id}")
    assert "знято" in tg.texts[0]
    assert any("⬜ Зарядка" in b for b in tg.buttons)


async def test_undo_uses_owner_day_when_bot_clock_is_on_previous_day(
    tg: BotUnderTest, monkeypatch
):
    class HostDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 12)

    monkeypatch.setattr(checkin, "date", HostDate, raising=False)
    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc),
    )
    habit = await tg.api.create_habit(USER.id, "Йога")
    await tg.api.check_in(USER.id, habit["id"])
    previous = await tg.api._request(
        "POST", f"/habits/{habit['id']}/checkins", USER.id, json={"day": "2026-09-12"}
    )
    assert previous.status_code == 201

    await tg.tap(f"habit:toggle:{habit['id']}")

    response = await tg.api._request("GET", f"/habits/{habit['id']}/checkins", USER.id)
    assert [checkin["day"] for checkin in response.json()] == ["2026-09-12"]
    assert tg.texts[0] == "Відмітку знято"


async def test_tapping_foreign_habit_does_not_change_its_owner(tg: BotUnderTest):
    habit = await tg.api.create_habit(999, "Чужа звичка")
    await tg.api.check_in(999, habit["id"])

    await tg.tap(f"habit:toggle:{habit['id']}")

    owner_habits = await tg.api.habits_with_stats(999)
    assert owner_habits[0]["stats"]["done_today"] is True
    assert await tg.api.list_habits(USER.id) == []
    assert not any(text in {"Відмічено 🔥", "Відмітку знято"} for text in tg.texts)


async def test_habits_list_counts_planned_habits_and_labels_rest_days(
    tg: BotUnderTest, monkeypatch
):
    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc),
    )
    await tg.api.create_habit(USER.id, "Щодня")
    for name, fields in [
        ("Відпочинок", {"weekdays": [5]}),
        ("Архів", {}),
    ]:
        response = await tg.api._request(
            "POST", "/habits", USER.id, json={"name": name, **fields}
        )
        assert response.status_code == 201, response.text
        if name == "Архів":
            response = await tg.api._request(
                "PATCH",
                f"/habits/{response.json()['id']}",
                USER.id,
                json={"archived": True},
            )
            assert response.status_code == 200, response.text

    await tg.send("/habits")

    assert "Сьогодні відмічено: 0 з 1" in tg.texts[0]
    assert "не заплановано" in tg.texts[0]
    assert "⬜ Щодня" in tg.buttons
    assert "💤 Відпочинок" in tg.buttons
    assert all("Архів" not in button for button in tg.buttons)


async def test_habit_card_explains_rest_day(tg: BotUnderTest):
    today = await tg.api.today(USER.id)
    response = await tg.api._request(
        "POST",
        "/habits",
        USER.id,
        json={"name": "Йога", "weekdays": [(today.weekday() + 1) % 7]},
    )
    assert response.status_code == 201, response.text
    habit = response.json()

    await tg.tap(f"habit:open:{habit['id']}")

    assert any("Сьогодні: 💤 не заплановано" in text for text in tg.texts)


async def test_tapping_edits_message_instead_of_sending_new(tg: BotUnderTest):
    """Чат не має заростати копіями списку після кожної відмітки."""
    habit_id = await create_habit_via_dialog(tg, "Зарядка")

    await tg.tap(f"habit:toggle:{habit_id}")

    kinds = [kind for kind, _, _ in tg.sent]
    assert "EditMessageText" in kinds
    assert "SendMessage" not in kinds


async def test_tapping_deleted_habit_explains_itself(tg: BotUnderTest):
    """Кнопка звички, видаленої у браузері, має пояснити, що сталося."""
    habit_id = await create_habit_via_dialog(tg, "Тимчасова")
    await tg.api.delete_habit(USER.id, habit_id)

    await tg.tap(f"habit:toggle:{habit_id}")

    assert "вже немає" in tg.texts[0]


# ---------- кнопки, що лишились у старих повідомленнях ----------


async def test_stale_button_is_always_answered(tg: BotUnderTest):
    """Регресійний тест на другу справжню помилку.

    «Пропустити» працює лише всередині свого стану. Натиснута зі старого,
    вже завершеного діалогу, вона не потрапляла до жодного обробника —
    і Telegram крутив на ній годинник до самого таймауту. Виглядало як
    зависший бот, хоча бот був живий-здоровий.
    """
    await tg.tap("menu:skip_description")

    kinds = [kind for kind, _, _ in tg.sent]
    assert "Alert" in kinds, "кнопка лишилась без відповіді — буде «годинник»"
    assert "неактуальна" in tg.texts[0]


async def test_new_habit_button_starts_dialog(tg: BotUnderTest):
    await tg.tap("menu:new_habit")

    assert "Як назвемо звичку" in tg.texts[-1]


# ---------- одночасність: подвійний тап і подвійний клік ----------
#
# Регресійні тести на знахідки повторного рев'ю, підтверджені прямим
# прогоном: два майже одночасні натискання оголюють гонки, яких не
# видно при послідовних викликах. asyncio.gather навмисно замінює тут
# tap()/send() — ці зручні методи чистять tg.sent ПЕРЕД кожним викликом,
# а для двох одночасних викликів це затирало б відповідь одне одному.


async def test_double_tap_does_not_crash(tg: BotUnderTest):
    """Два майже одночасні тапи по тій самій звичці не мають ронити 500.

    Корінь був у main.py: create_checkin перевіряв дублікат (find_checkin)
    і лише ПОТІМ вставляв рядок — між цими двома кроками вміщалась гонка.
    Обидва запити встигали пройти перевірку до того, як перший зробить
    commit, і другий INSERT впирався в UNIQUE(habit_id, day) необробленим
    IntegrityError. Прямий прогін ловив це 2 рази з 10; після виправлення
    (сам ендпоінт ловить IntegrityError і віддає чесний 409) — 0 з 10.
    """
    habit_id = await create_habit_via_dialog(tg, "Зарядка")

    tg.bot.session.sent.clear()
    u1, u2 = (
        tg.make_tap(f"habit:toggle:{habit_id}"),
        tg.make_tap(f"habit:toggle:{habit_id}"),
    )
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    alerts = [t for k, t, *_ in tg.sent if k == "Alert"]
    assert not any("не так" in a or "500" in a for a in alerts), (
        f"подвійний тап мав дати чисте відмітити+зняти, а не збій: {alerts}"
    )

    stats = next(
        h["stats"]
        for h in await tg.api.habits_with_stats(USER.id)
        if h["id"] == habit_id
    )
    # Один тап відмічає, другий одразу знімає — after both, done_today=False.
    assert stats["done_today"] is False


async def test_error_between_checkin_and_redraw_informs_user_once(
    tg: BotUnderTest, monkeypatch: pytest.MonkeyPatch
):
    """Обрив між check_in і перемальовуванням списку — без подвійної відповіді.

    Раніше callback.answer("Відмічено") викликався ДО habits_view().
    Якщо habits_view() падав, on_error намагався відповісти на ТОЙ САМИЙ
    callback ще раз — а чи дозволяє це Telegram, ніде не задокументовано.
    Тепер дані для перемальовування збираються ПЕРШИМИ: якщо вони впадуть,
    callback ще жодного разу не отримав відповіді, і on_error відповідає
    рівно один раз — без залежності від недокументованої поведінки.
    """
    habit_id = await create_habit_via_dialog(tg, "Зарядка")

    import bot.views

    calls = {"n": 0}
    original = bot.views.habits_view

    async def flaky(api, tid):
        calls["n"] += 1
        if calls["n"] == 1:
            from bot.api import ApiUnavailable

            raise ApiUnavailable("сервер тимчасово ліг")
        return await original(api, tid)

    monkeypatch.setattr("bot.checkin.habits_view", flaky)

    await tg.tap(f"habit:toggle:{habit_id}")

    alerts = [t for k, t, *_ in tg.sent if k == "Alert"]
    assert len(alerts) == 1, f"мало бути рівно одна відповідь на callback: {alerts}"
    assert "ліг" in alerts[0] or "недоступний" in alerts[0]

    # Відмітка в базі все одно поставилась — check_in устиг відпрацювати
    # до збою в перемальовуванні.
    stats = next(
        h["stats"]
        for h in await tg.api.habits_with_stats(USER.id)
        if h["id"] == habit_id
    )
    assert stats["done_today"] is True


# ---------- тап посеред діалогу створення ----------


async def test_tapping_habit_mid_dialog_does_not_silently_end_it(tg: BotUnderTest):
    """Натискання звички посеред /new не має тихо «закривати» діалог.

    До фіксу: toggle_checkin не питав стан, малював ЗВИЧАЙНИЙ список —
    найсильніший сигнал «діалог завершено» — а FSM насправді лишався
    на кроці NewHabit.name. Наступне побутове повідомлення людини тихо
    ставало назвою нової звички.
    """
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.send("/new")
    assert await tg.fsm_state() == "NewHabit:name"

    await tg.tap(f"habit:toggle:{habit_id}")

    assert "завершимо" in tg.texts[0]
    # Головне: стан НЕ зник і НЕ намалювався звичайний список.
    assert await tg.fsm_state() == "NewHabit:name"
    assert not any("Твої звички" in t for t in tg.texts)


# ---------- подвійний клік «Пропустити» ----------


async def test_double_click_create_makes_exactly_one_habit(tg: BotUnderTest):
    """Два майже одночасні кліки «Створити звичку» — рівно одна звичка.

    Раніше два виклики finish() могли обидва прочитати той самий стан
    ДО того, як перший його прибере: другий бачив порожні дані й казав
    людині «діалог загубився», хоча перший щойно все створив успішно.
    Лок у new_habit.py серіалізує виклики — другий чекає на першого
    і застає стан уже прибраним, без гонки.
    """
    await tg.send("/new")
    await tg.send("Читати")
    await tg.tap("menu:skip_description")

    tg.bot.session.sent.clear()
    u1 = tg.make_tap("sched:done:0")
    u2 = tg.make_tap("sched:done:0")
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    habits = await tg.api.list_habits(USER.id)
    assert [h["name"] for h in habits] == ["Читати"], (
        "мала створитись рівно одна звичка"
    )

    texts = [t for k, t, *_ in tg.sent if k == "SendMessage"]
    assert any("Готово" in t for t in texts)
    # Хибного "загубився" тепер бути не повинно — переможений виклик
    # застає стан уже ЧЕСНО прибраним (лок серіалізував виклики).
    assert not any("загубився" in t for t in texts)


# ---------- HTML-екранування власного імені ----------


async def test_start_escapes_html_in_own_name(tg: BotUnderTest):
    """Ім'я з Telegram, що містить «<», не має ламати розмітку /start.

    Бот працює з parse_mode=HTML на весь застосунок, а first_name людина
    вписує собі сама — «Ann<3» цілком реальний нікнейм. Без екранування
    Telegram відхиляв би геть КОЖЕН /start такої людини: привітання і
    список звичок вона не побачила б ніколи, доки не перейменується.

    views.py вже уникає цієї пастки для назв звичок — тепер той самий
    захист є і для чужого рядка, яким є ім'я користувача.
    """
    weird_user = User(id=5555, is_bot=False, first_name="Ann<3")
    tg.bot.session.sent.clear()
    await tg.dispatcher.feed_update(
        tg.bot,
        Update(
            update_id=999,
            message=Message(
                message_id=999,
                date=datetime.now(),
                chat=Chat(id=5555, type="private"),
                from_user=weird_user,
                text="/start",
            ),
        ),
    )

    raw = next(t for k, t, *_ in tg.sent if k == "SendMessage")
    assert "Ann&lt;3" in raw
    assert "Ann<3" not in raw


# ---------- стікер/фото на кроці опису ----------


async def test_photo_on_description_step_gets_explanation(tg: BotUnderTest):
    """Стікер чи фото замість опису — бот пояснює, а не мовчить.

    Дзеркальна поломка до вже виправленої на кроці назви: got_description
    вимагає F.text, і без спеціального обробника оновлення без тексту
    тонуло без сліду. Бот виглядав зависшим, хоча просто чекав текст.
    """
    await tg.send("/new")
    await tg.send("Читати")

    await tg.photo("мій опис у підписі")

    assert "Потрібен текст" in tg.texts[0]
    assert await tg.fsm_state() == "NewHabit:description"
    assert await tg.api.list_habits(USER.id) == []


# ---------- секрет не з латиниці ----------


def test_non_ascii_secret_blocks_startup(monkeypatch: pytest.MonkeyPatch):
    """Кириличний BOT_SECRET має зупиняти старт із зрозумілим текстом.

    Без цієї перевірки непорожній кириличний секрет проходив би повз
    check_settings() мовчки, а перша ж дія в боті падала б голим
    UnicodeEncodeError (httpx кодує заголовки в ascii) — і людина не
    отримувала б жодного натяку на BOT_SECRET чи .env.
    """
    import bot.__main__ as entry

    monkeypatch.setattr(entry, "BOT_TOKEN", "123:fake")
    monkeypatch.setattr(entry, "BOT_SECRET", "мій-секрет")
    with pytest.raises(SystemExit):
        entry.check_settings()

    monkeypatch.setattr(entry, "BOT_SECRET", "latin-secret-ok")
    entry.check_settings()  # не має кидати нічого


# ---------- shorten() і прапори ----------


def test_shorten_does_not_split_flag_emoji():
    """Обрізання довгої назви не лишає розрізаний навпіл прапор.

    Прапор у Unicode — пара regional-indicator символів. Наївне
    text[:N] могло розрізати таку пару, лишаючи на кнопці самотню
    літеру в квадратній рамці замість прапора.
    """
    from bot.keyboards import shorten

    result = shorten("🇺🇦" * 50)  # 100 code points, > MAX_BUTTON_TEXT

    regional_indicators = sum(1 for c in result if 0x1F1E6 <= ord(c) <= 0x1F1FF)
    assert regional_indicators % 2 == 0, f"розрізаний прапор у {result!r}"


# ---------- керування звичками ----------


async def test_manage_button_opens_management_list(tg: BotUnderTest):
    await create_habit_via_dialog(tg, "Йога")

    await tg.tap("menu:manage")

    assert "Керування звичками" in tg.texts[-1]
    # У режимі керування — без ✅/⬜: тут не відмічають, а порядкують.
    assert "Йога" in tg.buttons
    assert not any(b.startswith(("✅", "⬜")) for b in tg.buttons)


async def test_back_from_manage_returns_to_habits(tg: BotUnderTest):
    await create_habit_via_dialog(tg, "Йога")
    await tg.tap("menu:manage")

    await tg.tap("menu:habits")

    assert "Твої звички" in tg.texts[-1]
    assert any("Йога" in b and b.startswith("⬜") for b in tg.buttons)


async def test_habit_card_shows_stats_and_description(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send("Зарядка")
    await tg.send("одразу після пробудження")
    await tg.tap("sched:done:0")

    habit_id = (await tg.api.list_habits(USER.id))[0]["id"]
    await tg.api.check_in(USER.id, habit_id)

    await tg.tap(f"habit:open:{habit_id}")

    card = tg.texts[-1]
    assert "Зарядка" in card
    assert "одразу після пробудження" in card
    assert "відмічено" in card
    assert "Серія: 1 день" in card  # відмінювання: саме "день", не "днів"


async def test_habit_card_escapes_html_in_name(tg: BotUnderTest):
    """Назва потрапляє в HTML-текст картки, тож має бути екранована.

    У списку назви живуть лише на кнопках, де розмітка не обробляється.
    Картка — перший екран, де назва йде в сам текст, і незекранований
    «<» тут відхилив би повідомлення цілком, як колись ламав /start.
    """
    await tg.send("/new")
    await tg.send("Читати <Дюну>")
    await tg.tap("menu:skip_description")
    await tg.tap("sched:done:0")

    habit_id = (await tg.api.list_habits(USER.id))[0]["id"]
    await tg.tap(f"habit:open:{habit_id}")

    card = tg.texts[-1]
    assert "&lt;Дюну&gt;" in card
    assert "<Дюну>" not in card


async def test_rename_habit(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:rename:{habit_id}")
    assert "нову назву" in tg.texts[-1]

    await tg.send("Ранкова йога")

    assert "Збережено" in tg.texts[-1]
    habits = await tg.api.list_habits(USER.id)
    assert [h["name"] for h in habits] == ["Ранкова йога"]


async def test_rename_keeps_description(tg: BotUnderTest):
    """Зміна назви не має затирати опис.

    На боці API це забезпечує exclude_unset, а на боці бота — те, що
    update_habit кладе в тіло лише передані поля. Якби він завжди слав
    обидва, опис перетворився б на порожній рядок.
    """
    await tg.send("/new")
    await tg.send("Йога")
    await tg.send("щоранку 20 хвилин")
    await tg.tap("sched:done:0")

    habit_id = (await tg.api.list_habits(USER.id))[0]["id"]

    await tg.tap(f"habit:rename:{habit_id}")
    await tg.send("Ранкова йога")

    habit = (await tg.api.list_habits(USER.id))[0]
    assert habit["name"] == "Ранкова йога"
    assert habit["description"] == "щоранку 20 хвилин"


async def test_change_description(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:describe:{habit_id}")
    await tg.send("новий опис")

    habit = (await tg.api.list_habits(USER.id))[0]
    assert habit["description"] == "новий опис"


async def test_clear_description_with_marker(tg: BotUnderTest):
    """Порожнє повідомлення надіслати не можна, тому опис прибирає «-»."""
    await tg.send("/new")
    await tg.send("Йога")
    await tg.send("зайвий опис")
    await tg.tap("sched:done:0")

    habit_id = (await tg.api.list_habits(USER.id))[0]["id"]

    await tg.tap(f"habit:describe:{habit_id}")
    await tg.send("-")

    habit = (await tg.api.list_habits(USER.id))[0]
    assert habit["description"] == ""


async def test_command_during_rename_does_not_become_the_name(tg: BotUnderTest):
    """Та сама пастка, що й у діалозі створення — і той самий захист."""
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:rename:{habit_id}")

    await tg.send("/habits")

    assert "завершимо редагування" in tg.texts[0]
    assert (await tg.api.list_habits(USER.id))[0]["name"] == "Йога"


async def test_cancel_rename_keeps_old_name(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:rename:{habit_id}")

    await tg.send("/cancel")

    assert "Скасовано" in tg.texts[0]
    assert (await tg.api.list_habits(USER.id))[0]["name"] == "Йога"


async def test_tapping_habit_during_rename_is_refused(tg: BotUnderTest):
    """Відмічати посеред редагування не можна — інакше екран збрехав би.

    Перевірка в checkin.py дивиться на «є будь-який стан», а не на
    перелік станів створення. Цей тест стереже саме те, що вона
    поширюється й на новий діалог редагування.
    """
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:rename:{habit_id}")

    await tg.tap(f"habit:toggle:{habit_id}")

    assert "Спершу завершимо" in tg.texts[0]
    stats = next(
        h["stats"]
        for h in await tg.api.habits_with_stats(USER.id)
        if h["id"] == habit_id
    )
    assert stats["done_today"] is False


# ---------- видалення ----------


async def test_delete_asks_for_confirmation_first(tg: BotUnderTest):
    """Кошик не видаляє одразу — спершу питає, і називає ціну."""
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.api.check_in(USER.id, habit_id)

    await tg.tap(f"habit:delete:{habit_id}")

    assert "Видалити" in tg.texts[-1]
    assert "незворотно" in tg.texts[-1]
    assert "1 день" in tg.texts[-1]  # скільки відміток зникне
    # Головне: звичка ще на місці.
    assert len(await tg.api.list_habits(USER.id)) == 1


async def test_delete_confirmed_removes_habit(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:delete:{habit_id}")
    await tg.tap(f"habit:confirm_delete:{habit_id}")

    assert await tg.api.list_habits(USER.id) == []


async def test_delete_cancelled_keeps_habit(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:delete:{habit_id}")
    await tg.tap(f"habit:open:{habit_id}")  # «Скасувати» веде в картку

    assert len(await tg.api.list_habits(USER.id)) == 1
    assert "Йога" in tg.texts[-1]


# ---------- архів ----------


async def test_card_offers_archive_above_delete(tg: BotUnderTest):
    """Безпечніша альтернатива видаленню має трапитись на очі першою."""
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:open:{habit_id}")

    assert tg.buttons.index("📦 В архів") < tg.buttons.index("🗑 Видалити")


async def test_archive_hides_habit_but_keeps_history(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.api.check_in(USER.id, habit_id)

    await tg.tap(f"habit:archive:{habit_id}")

    assert tg.sent[0] == ("Alert", "Перенесено в архів 📦", [])
    # Одразу видно результат: звички немає серед активних, є вхід в архів.
    assert "Йога" not in tg.buttons
    assert "📦 Архів (1)" in tg.buttons
    # Зі списку відміток (і нагадувань) звичка зникла…
    assert await tg.api.list_habits(USER.id) == []
    # …але відмітки лишились — саме цим архів і відрізняється від видалення.
    response = await tg.api._request("GET", f"/habits/{habit_id}/checkins", USER.id)
    assert len(response.json()) == 1


async def test_archive_needs_no_confirmation(tg: BotUnderTest):
    """На відміну від 🗑: один дотик — і готово, бо це легко скасувати."""
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:archive:{habit_id}")

    assert await tg.api.list_habits(USER.id) == []
    assert "незворотно" not in " ".join(tg.texts)


async def test_archived_card_offers_restore_and_delete_only(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:archive:{habit_id}")

    await tg.tap("menu:archive")
    assert "📦 Йога" in tg.buttons

    await tg.tap(f"habit:open:{habit_id}")

    card = tg.texts[-1]
    assert "В архіві з" in card
    # «Сьогодні: ще ні» для звички на паузі звучало б як докір.
    assert "Сьогодні" not in card
    assert tg.buttons == ["♻️ Відновити", "🗑 Видалити", "⬅️ До архіву"]


async def test_restore_returns_habit_to_list(tg: BotUnderTest):
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:archive:{habit_id}")

    await tg.tap(f"habit:unarchive:{habit_id}")

    assert tg.sent[0] == ("Alert", "Відновлено ♻️", [])
    assert [h["id"] for h in await tg.api.list_habits(USER.id)] == [habit_id]
    # Картка вже активної звички — з редагуванням і кнопкою архіву.
    assert "📦 В архів" in tg.buttons


async def test_delete_from_archive_works(tg: BotUnderTest):
    """Без include_archived архівна звичка виглядала б «уже видаленою»."""
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:archive:{habit_id}")

    await tg.tap(f"habit:delete:{habit_id}")
    assert "незворотно" in tg.texts[-1]
    await tg.tap(f"habit:confirm_delete:{habit_id}")

    everything = await tg.api.list_habits(USER.id, include_archived=True)
    assert everything == []


async def test_manage_with_everything_archived_leads_to_archive(tg: BotUnderTest):
    """«Керувати нічим» було б неправдою, коли архів не порожній."""
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:archive:{habit_id}")

    await tg.send("/manage")

    assert "усі в архіві" in tg.texts[-1]
    assert tg.buttons == ["📦 Архів (1)", "⬅️ До списку"]


async def test_double_archive_tap_keeps_original_date(tg: BotUnderTest, monkeypatch):
    """Повторний дотик по старій кнопці не зсуває дату архіву."""
    monkeypatch.setattr(
        calendar_rules, "now_utc", lambda: datetime(2026, 9, 10, 9, tzinfo=timezone.utc)
    )
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:archive:{habit_id}")

    monkeypatch.setattr(
        calendar_rules, "now_utc", lambda: datetime(2026, 9, 12, 9, tzinfo=timezone.utc)
    )
    await tg.tap(f"habit:archive:{habit_id}")

    assert tg.sent[0] == ("Alert", "Перенесено в архів 📦", [])
    (habit,) = await tg.api.list_habits(USER.id, include_archived=True)
    assert habit["archived_at"] == "2026-09-10"


# ---------- відмітка за вчора в картці ----------


def set_clock(monkeypatch, day: int, hour: int = 9) -> None:
    """Годинник API на вересень 2026 (UTC). Київ = UTC+3, тож 9:00 — той самий день."""
    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, day, hour, tzinfo=timezone.utc),
    )


async def habit_from_past(tg: BotUnderTest, monkeypatch, **kwargs) -> int:
    """Звичка, створена 10.09, а «сьогодні» — 12.09: учора (11.09) вона вже була."""
    set_clock(monkeypatch, 10)
    habit = await tg.api.create_habit(USER.id, "Йога", **kwargs)
    set_clock(monkeypatch, 12)
    return habit["id"]


async def test_card_offers_marking_yesterday_with_date(tg: BotUnderTest, monkeypatch):
    habit_id = await habit_from_past(tg, monkeypatch)

    await tg.tap(f"habit:open:{habit_id}")

    # Першим рядком і з датою: після опівночі «вчора» без дати заплутало б.
    assert tg.buttons[0] == "↩️ Відмітити вчора (11.09)"


async def test_marking_yesterday_saves_that_day(tg: BotUnderTest, monkeypatch):
    habit_id = await habit_from_past(tg, monkeypatch)

    await tg.tap(f"day:mark:{habit_id}:2026-09-11")

    assert await checkin_days(tg, habit_id) == ["2026-09-11"]
    assert tg.sent[0] == ("Alert", "Відмічено за 11.09 ✅", [])
    # Картка перемальована: кнопка тепер пропонує зняти.
    assert tg.buttons[0] == "✅ Вчора відмічено (11.09) · зняти"


async def test_unmarking_yesterday_removes_only_that_day(tg: BotUnderTest, monkeypatch):
    habit_id = await habit_from_past(tg, monkeypatch)
    await tg.api.check_in(USER.id, habit_id)  # сьогодні, 12.09
    await tg.tap(f"day:mark:{habit_id}:2026-09-11")

    await tg.tap(f"day:unmark:{habit_id}:2026-09-11")

    assert await checkin_days(tg, habit_id) == ["2026-09-12"]
    assert tg.sent[0] == ("Alert", "Відмітку за 11.09 знято", [])
    assert tg.buttons[0] == "↩️ Відмітити вчора (11.09)"


async def test_stale_mark_button_does_not_unmark(tg: BotUnderTest, monkeypatch):
    """Кнопка «Відмітити» зі старої картки, а день уже відмітили у браузері.

    Перемикач зняв би відмітку — протилежне до написаного на кнопці.
    """
    habit_id = await habit_from_past(tg, monkeypatch)
    await tg.api.check_in(USER.id, habit_id, date(2026, 9, 11))

    await tg.tap(f"day:mark:{habit_id}:2026-09-11")

    assert tg.sent[0] == ("Alert", "Цей день уже відмічено", [])
    assert await checkin_days(tg, habit_id) == ["2026-09-11"]


async def test_new_habit_has_no_yesterday_button(tg: BotUnderTest):
    """Звичка, створена сьогодні, вчора ще не існувала — «наздоганяти» нічого."""
    habit_id = await create_habit_via_dialog(tg, "Йога")

    await tg.tap(f"habit:open:{habit_id}")

    assert not any("вчора" in button.lower() for button in tg.buttons)


async def test_no_yesterday_button_when_yesterday_was_off_schedule(
    tg: BotUnderTest, monkeypatch
):
    # 11.09.2026 — п'ятниця; звичка лише по понеділках.
    habit_id = await habit_from_past(tg, monkeypatch, weekdays=[0])

    await tg.tap(f"habit:open:{habit_id}")

    assert not any("вчора" in button.lower() for button in tg.buttons)


@pytest.mark.parametrize(
    "day",
    [
        "2026-09-12",  # сьогодні — для цього є звичайна кнопка в /habits
        "2030-01-01",  # майбутнє: API таке прийняв би й роздув серію наперед
        "not-a-day",
    ],
)
async def test_forged_day_button_changes_nothing(
    tg: BotUnderTest, monkeypatch, day: str
):
    habit_id = await habit_from_past(tg, monkeypatch)

    await tg.tap(f"day:mark:{habit_id}:{day}")

    assert await checkin_days(tg, habit_id) == []
    assert [kind for kind, _, _ in tg.sent] == ["Alert"]
    assert "неактуальна" in tg.texts[0]


async def test_forged_future_reminder_button_changes_nothing(
    tg: BotUnderTest, monkeypatch
):
    """Та сама діра в кнопці нагадування: дата в ній теж від клієнта."""
    habit_id = await habit_from_past(tg, monkeypatch)

    await tap_reminder(tg, habit_id, "2030-01-01", None)

    assert await checkin_days(tg, habit_id) == []


async def test_double_confirm_delete_is_harmless(tg: BotUnderTest):
    """Два натискання «Так, видалити» не мають дати помилку.

    Друге натискання приходить на вже видалену звичку. Без перевірки
    воно перетворилося б на 404 і технічний текст замість спокійного
    «усе вже зроблено».
    """
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.tap(f"habit:delete:{habit_id}")

    tg.bot.session.sent.clear()
    u1 = tg.make_tap(f"habit:confirm_delete:{habit_id}")
    u2 = tg.make_tap(f"habit:confirm_delete:{habit_id}")
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    assert await tg.api.list_habits(USER.id) == []
    alerts = [t for k, t, *_ in tg.sent if k == "Alert"]
    assert not any("не так" in a or "404" in a for a in alerts), alerts


async def test_card_of_deleted_habit_explains_itself(tg: BotUnderTest):
    """Кнопка картки зі старого повідомлення після видалення звички."""
    habit_id = await create_habit_via_dialog(tg, "Йога")
    await tg.api.delete_habit(USER.id, habit_id)

    await tg.tap(f"habit:open:{habit_id}")

    alerts = [t for k, t, *_ in tg.sent if k == "Alert"]
    assert any("вже немає" in a for a in alerts), alerts


# ---------- розклад у діалозі створення ----------


async def reach_schedule_step(tg: BotUnderTest, name: str = "Спортзал") -> None:
    await tg.send("/new")
    await tg.send(name)
    await tg.tap("menu:skip_description")


async def test_schedule_is_chosen_in_place_and_saved(tg: BotUnderTest):
    """Раніше бот створював ЛИШЕ щоденні звички: create_habit не передавав
    weekdays, а API мовчки ставив «щодня». Тепер розклад обирається в діалозі."""
    await reach_schedule_step(tg)

    await tg.tap("sched:workdays:0")
    kind, text, buttons = tg.sent[-1]
    # Те саме повідомлення перемальовується, а не шлеться нове.
    assert kind == "EditMessageText"
    assert "Зараз: <b>Пн, Вт, Ср, Чт, Пт</b>" in text
    assert "▫️ Сб" in buttons

    await tg.tap("sched:toggle:2")
    assert "Зараз: <b>Пн, Вт, Чт, Пт</b>" in tg.sent[-1][1]

    await tg.tap("sched:done:0")
    habits = await tg.api.list_habits(USER.id)
    assert habits[0]["weekdays"] == [0, 1, 3, 4]


async def test_schedule_needs_at_least_one_day(tg: BotUnderTest):
    await reach_schedule_step(tg)
    await tg.tap("sched:workdays:0")
    for day in range(5):
        await tg.tap(f"sched:toggle:{day}")
    assert "Зараз: <b>жодного дня</b>" in tg.sent[-1][1]

    await tg.tap("sched:done:0")

    assert ("Alert", "Обери хоча б один день.", []) in tg.sent
    assert await tg.api.list_habits(USER.id) == []
    # Діалог не зламався: можна обрати день і завершити.
    assert await tg.fsm_state() == "NewHabit:weekdays"


async def test_forged_day_in_button_is_ignored(tg: BotUnderTest):
    """Дані кнопки приходять від клієнта; день 99 не має дожити до API."""
    await reach_schedule_step(tg)

    await tg.tap("sched:toggle:99")
    await tg.tap("sched:done:0")

    habits = await tg.api.list_habits(USER.id)
    assert habits[0]["weekdays"] == [0, 1, 2, 3, 4, 5, 6]


async def test_text_on_schedule_step_gets_explanation(tg: BotUnderTest):
    await reach_schedule_step(tg)

    await tg.send("понеділок і середа")

    assert "кнопками" in tg.texts[0]
    assert await tg.fsm_state() == "NewHabit:weekdays"


async def test_cancel_on_schedule_step_creates_nothing(tg: BotUnderTest):
    await reach_schedule_step(tg)
    await tg.send("/cancel")

    assert "Скасовано" in tg.texts[0]
    assert await tg.api.list_habits(USER.id) == []


async def test_double_click_skip_shows_one_schedule_prompt(tg: BotUnderTest):
    """Подвійний «Пропустити» не має надіслати питання про розклад двічі."""
    await tg.send("/new")
    await tg.send("Читати")

    tg.bot.session.sent.clear()
    u1 = tg.make_tap("menu:skip_description")
    u2 = tg.make_tap("menu:skip_description")
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    prompts = [
        t for k, t, *_ in tg.sent if k == "SendMessage" and "Коли виконувати" in t
    ]
    assert len(prompts) == 1


async def test_habit_card_shows_schedule(tg: BotUnderTest):
    response = await tg.api._request(
        "POST", "/habits", USER.id, json={"name": "Спортзал", "weekdays": [0, 2, 4]}
    )
    assert response.status_code == 201, response.text

    await tg.tap(f"habit:open:{response.json()['id']}")

    assert any("📅 Розклад: Пн, Ср, Пт" in text for text in tg.texts)


def test_schedule_label_matches_web_wording():
    from bot.schedule import schedule_label

    assert schedule_label([0, 1, 2, 3, 4, 5, 6]) == "Щодня"
    assert schedule_label([4, 0, 2]) == "Пн, Ср, Пт"


def test_toggle_day_keeps_list_sorted():
    from bot.schedule import toggle_day

    assert toggle_day([0, 4], 2) == [0, 2, 4]
    assert toggle_day([0, 2, 4], 2) == [0, 4]


# ---------- кнопки під нагадуванням ----------


async def tap_reminder(tg: BotUnderTest, habit_id: int, day: str, markup) -> None:
    """Натискання кнопки під нагадуванням.

    На відміну від tap(), повідомлення тут несе клавіатуру: обробник
    дізнається з неї, які звички ще лишились невідміченими.
    """
    tg._update_id += 1
    tg.bot.session.sent.clear()
    await tg.feed(
        Update(
            update_id=tg._update_id,
            callback_query=CallbackQuery(
                id=str(tg._update_id),
                from_user=USER,
                chat_instance="test",
                data=f"rem:{habit_id}:{day}",
                message=Message(
                    message_id=tg._update_id,
                    date=datetime.now(),
                    chat=CHAT,
                    text="нагадування",
                    reply_markup=markup,
                ),
            ),
        )
    )


async def checkin_days(tg: BotUnderTest, habit_id: int) -> list[str]:
    response = await tg.api._request("GET", f"/habits/{habit_id}/checkins", USER.id)
    return [item["day"] for item in response.json()]


async def test_reminder_button_marks_and_removes_habit(tg: BotUnderTest):
    from bot.keyboards import reminder_keyboard

    yoga = await tg.api.create_habit(USER.id, "Йога")
    reading = await tg.api.create_habit(USER.id, "Читати")
    today = await tg.api.today(USER.id)
    markup = reminder_keyboard([yoga, reading], today)

    await tap_reminder(tg, yoga["id"], today.isoformat(), markup)

    assert await checkin_days(tg, yoga["id"]) == [today.isoformat()]
    assert tg.sent[0] == ("Alert", "Відмічено 🔥", [])
    kind, text, buttons = tg.sent[1]
    assert kind == "EditMessageText"
    # Відмічена звичка зникає і з тексту, і з кнопок; решта лишається.
    assert "Читати" in text and "Йога" not in text
    assert buttons == ["⬜ Читати", "📋 Усі звички"]


async def test_last_reminder_button_says_all_done(tg: BotUnderTest):
    from bot.keyboards import reminder_keyboard

    yoga = await tg.api.create_habit(USER.id, "Йога")
    today = await tg.api.today(USER.id)

    await tap_reminder(
        tg, yoga["id"], today.isoformat(), reminder_keyboard([yoga], today)
    )

    _, text, buttons = tg.sent[1]
    assert "Усе відмічено" in text
    assert buttons == ["📋 Усі звички"]


async def test_second_tap_on_reminder_does_not_unmark(tg: BotUnderTest):
    """Нагадування просить «зроби» — повторний дотик не знімає відмітку.

    Стара клавіатура (кнопка ще на місці) — так буває, коли людина тисне
    двічі швидше, ніж Telegram встиг перемалювати повідомлення.
    """
    from bot.keyboards import reminder_keyboard

    yoga = await tg.api.create_habit(USER.id, "Йога")
    today = await tg.api.today(USER.id)
    markup = reminder_keyboard([yoga], today)

    await tap_reminder(tg, yoga["id"], today.isoformat(), markup)
    await tap_reminder(tg, yoga["id"], today.isoformat(), markup)

    assert tg.sent[0] == ("Alert", "Уже відмічено ✅", [])
    assert await checkin_days(tg, yoga["id"]) == [today.isoformat()]


async def test_reminder_button_after_midnight_marks_reminder_day(
    tg: BotUnderTest, monkeypatch
):
    """Нагадування за 12 вересня, натиснуте о 00:30 13-го за Києвом.

    Звичайна кнопка відмітила б «сьогодні» сервера — тобто 13-те.
    А людина відповідала на нагадування про 12-те, і саме його пропустила.
    """
    from bot.keyboards import reminder_keyboard

    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, 12, 21, 30, tzinfo=timezone.utc),
    )
    yoga = await tg.api.create_habit(USER.id, "Йога")
    assert (await tg.api.today(USER.id)).isoformat() == "2026-09-13"

    await tap_reminder(
        tg, yoga["id"], "2026-09-12", reminder_keyboard([yoga], date(2026, 9, 12))
    )

    assert await checkin_days(tg, yoga["id"]) == ["2026-09-12"]


async def test_forged_reminder_day_is_answered_without_checkin(tg: BotUnderTest):
    yoga = await tg.api.create_habit(USER.id, "Йога")

    await tap_reminder(tg, yoga["id"], "not-a-day", None)

    assert [kind for kind, _, _ in tg.sent] == ["Alert"]
    assert "неактуальна" in tg.texts[0]
    assert await checkin_days(tg, yoga["id"]) == []


async def test_reminder_button_on_foreign_habit_changes_nothing(tg: BotUnderTest):
    """id звички в кнопці теж від клієнта: чужу звичку відмітити не можна."""
    foreign = await tg.api.create_habit(9999, "Чужа")
    today = await tg.api.today(USER.id)

    await tap_reminder(tg, foreign["id"], today.isoformat(), None)

    response = await tg.api._request("GET", f"/habits/{foreign['id']}/checkins", 9999)
    assert response.json() == []
    assert [kind for kind, _, _ in tg.sent] == ["Alert"]


# ---------- /settings ----------


async def me(tg: BotUnderTest) -> dict:
    return await tg.api.get_me(USER.id)


async def test_settings_shows_current_values(tg: BotUnderTest):
    await tg.send("/settings")

    text = tg.texts[0]
    assert "увімкнені, о 20:00" in text
    assert "Europe/Kyiv" in text
    assert tg.buttons == [
        "🔕 Вимкнути нагадування",
        "🕗 Змінити годину",
        "🌍 Змінити пояс",
        "⬅️ До списку",
    ]


async def test_turn_reminders_off_and_on(tg: BotUnderTest):
    """Раніше єдиним способом зупинити нагадування з Telegram було заблокувати бота."""
    await tg.tap("set:reminders:off")

    assert (await me(tg))["reminders_enabled"] is False
    assert tg.sent[0] == ("Alert", "Нагадування вимкнено 🔕", [])
    assert "вимкнені" in tg.texts[-1]
    assert tg.buttons[0] == "🔔 Увімкнути нагадування"

    await tg.tap("set:reminders:on")

    assert (await me(tg))["reminders_enabled"] is True


async def test_disabled_reminders_are_not_sent(tg: BotUnderTest, monkeypatch):
    """Кнопка справді зупиняє розсилку, а не лише змінює текст на екрані."""
    from bot.reminders import send_reminders

    monkeypatch.setattr(
        calendar_rules,
        "now_utc",
        lambda: datetime(2026, 9, 12, 17, tzinfo=timezone.utc),  # 20:00 у Києві
    )
    await tg.api.create_habit(USER.id, "Йога")
    now = datetime(2026, 9, 12, 17, tzinfo=timezone.utc)

    # Контроль: з увімкненими нагадуваннями ця сама ситуація ДАЄ нагадування.
    # Без цього кроку тест пройшов би й тоді, коли нагадування не йде з
    # якоїсь іншої причини, а кнопка вимкнення зламана.
    tg.bot.session.sent.clear()
    await send_reminders(tg.bot, tg.api, now=now)
    assert any("Нагадування" in text for text in tg.texts)

    # Наступного дня — щоб «уже надсилали сьогодні» не маскувало результат.
    tomorrow = now + timedelta(days=1)
    monkeypatch.setattr(calendar_rules, "now_utc", lambda: tomorrow)
    await tg.tap("set:reminders:off")

    tg.bot.session.sent.clear()
    await send_reminders(tg.bot, tg.api, now=tomorrow)

    assert tg.sent == []


async def test_change_reminder_hour(tg: BotUnderTest):
    await tg.tap("set:hours:")
    assert "✅20" in tg.buttons
    assert len([b for b in tg.buttons if b[-2:].isdigit()]) == 24

    await tg.tap("set:hour:7")

    assert (await me(tg))["reminder_hour"] == 7
    assert tg.sent[0] == ("Alert", "Нагадування о 07:00", [])
    assert "о 07:00" in tg.texts[-1]


async def test_pick_preset_timezone(tg: BotUnderTest):
    await tg.tap("set:zones:")
    assert "✅ Europe/Kyiv" in tg.buttons

    await tg.tap("set:zone:Europe/Warsaw")

    assert (await me(tg))["timezone"] == "Europe/Warsaw"
    assert "Europe/Warsaw" in tg.texts[-1]


@pytest.mark.parametrize(
    "data",
    [
        "set:hour:99",
        "set:hour:-1",
        "set:hour:abc",
        # isdigit() каже True, а int() падає (²) або дає несподіване (٣ → 3):
        # від клієнта приймаємо лише ASCII-цифри.
        "set:hour:²",
        "set:hour:٣",
        "set:hour:+7",
        "set:reminders:maybe",
        "set:zone:Mars/Olympus",  # не з готового списку — навіть якби API прийняв
        "set:drop_tables:",
    ],
)
async def test_forged_settings_buttons_change_nothing(tg: BotUnderTest, data: str):
    before = await me(tg)

    await tg.tap(data)

    assert await me(tg) == before
    assert [kind for kind, _, _ in tg.sent] == ["Alert"]
    assert "неактуальна" in tg.texts[0]


async def test_manual_timezone_is_canonicalized(tg: BotUnderTest):
    """«new york» → America/New_York: у базу — лише канонічна назва."""
    await tg.tap("set:zone_manual:")
    assert await tg.fsm_state() is not None

    await tg.send("new york")

    assert (await me(tg))["timezone"] == "America/New_York"
    assert "Збережено" in tg.texts[0]
    assert await tg.fsm_state() is None


@pytest.mark.parametrize(
    ("typed", "saved"),
    [
        ("europe/kyiv", "Europe/Kyiv"),  # повна назва, інший регістр
        ("  UTC  ", "UTC"),
        ("warsaw", "Europe/Warsaw"),  # лише місто
        # Зсуви відхиляємо: в Etc/GMT±N знак ПЕРЕВЕРНУТИЙ (POSIX), тож
        # «GMT+3» від людини з Києва означав би UTC−3 — нагадування о 02:00.
        ("GMT+3", None),
        ("gmt-2", None),
        ("Etc/GMT+3", None),
        ("UTC+3", None),
    ],
)
def test_resolve_timezone_returns_canonical_name(typed: str, saved: str | None):
    """На Windows ZoneInfo("europe/kyiv") «працює» через нечутливу до регістру
    файлову систему — і без канонізації в базу ліг би пояс, якого на
    Linux-сервері не існує."""
    assert settings.resolve_timezone(typed) == saved


async def test_unknown_manual_timezone_keeps_dialog(tg: BotUnderTest):
    await tg.tap("set:zone_manual:")

    await tg.send("Марс <Олімп>")

    assert (await me(tg))["timezone"] == "Europe/Kyiv"
    # Введене людиною повертається в текст — тож екрановане.
    assert "Марс &lt;Олімп&gt;" in tg.texts[0]
    # Лишаємося в діалозі — можна просто надіслати іншу назву.
    await tg.send("Berlin")
    assert (await me(tg))["timezone"] == "Europe/Berlin"


async def test_offset_timezone_gets_explanation_and_keeps_dialog(tg: BotUnderTest):
    await tg.tap("set:zone_manual:")

    await tg.send("GMT+3")

    assert (await me(tg))["timezone"] == "Europe/Kyiv"
    assert "Europe/Kyiv" in tg.texts[0] and "місто" in tg.texts[0]
    assert await tg.fsm_state() is not None


@pytest.mark.parametrize("next_step", ["set:zone:Europe/Warsaw", "set:home:"])
async def test_other_settings_button_closes_timezone_input(
    tg: BotUnderTest, next_step: str
):
    """Натиснув «Ввести вручну», передумав і обрав кнопку — діалог не має зависнути.

    Інакше /habits відповідав би «Спершу надішли назву поясу», а кнопки
    відміток — «Спершу завершимо почате», хоча пояс уже збережено.
    """
    await tg.tap("set:zone_manual:")

    await tg.tap(next_step)

    assert await tg.fsm_state() is None
    await tg.send("/habits")
    assert "Твої звички" in tg.texts[0] or "немає жодної звички" in tg.texts[0]


async def test_settings_command_closes_timezone_input(tg: BotUnderTest):
    await tg.tap("set:zone_manual:")

    await tg.send("/settings")

    assert await tg.fsm_state() is None
    assert "Налаштування" in tg.texts[0]


async def test_settings_button_does_not_close_habit_dialog(tg: BotUnderTest):
    """Скидати можна лише СВІЙ стан — недописана звичка має вижити."""
    await tg.send("/new")
    state_before = await tg.fsm_state()

    await tg.tap("set:hour:7")

    assert await tg.fsm_state() == state_before


async def test_command_during_timezone_input_is_not_a_timezone(tg: BotUnderTest):
    await tg.tap("set:zone_manual:")

    await tg.send("/habits")

    assert "Спершу надішли назву поясу" in tg.texts[0]
    await tg.send("/cancel")
    assert await tg.fsm_state() is None
    assert (await me(tg))["timezone"] == "Europe/Kyiv"


async def test_manual_timezone_button_does_not_break_habit_dialog(tg: BotUnderTest):
    """Посеред /new «Ввести вручну» не має мовчки затерти недописану звичку."""
    await tg.send("/new")
    state_before = await tg.fsm_state()

    await tg.tap("set:zone_manual:")

    assert await tg.fsm_state() == state_before
    assert "Спершу завершимо почате" in tg.texts[0]


async def test_settings_command_mid_habit_dialog_does_not_become_name(
    tg: BotUnderTest,
):
    """/settings посеред /new відкриває налаштування, а не стає назвою звички."""
    await tg.send("/new")

    await tg.send("/settings")

    assert await tg.api.list_habits(USER.id) == []
    assert "Налаштування" in tg.texts[0]


def test_main_registers_settings_before_dialogs():
    import inspect

    import bot.__main__ as entry

    source = inspect.getsource(entry.main)
    assert source.index("settings.router") < source.index("new_habit.router")
    assert any(command.command == "settings" for command in entry.COMMANDS)


# ---------- донати в Stars (/support) ----------


async def pre_checkout(
    tg: BotUnderTest, payload: str, amount: int, currency: str = "XTR"
) -> None:
    """Telegram питає бота «приймаєш цей платіж?» — крок перед списанням."""
    tg._update_id += 1
    tg.bot.session.sent.clear()
    await tg.feed(
        Update(
            update_id=tg._update_id,
            pre_checkout_query=PreCheckoutQuery(
                id=str(tg._update_id),
                from_user=USER,
                currency=currency,
                total_amount=amount,
                invoice_payload=payload,
            ),
        )
    )


async def paid(tg: BotUnderTest, amount: int) -> None:
    """Гроші списано — Telegram присилає повідомлення з successful_payment."""
    tg._update_id += 1
    tg.bot.session.sent.clear()
    await tg.feed(
        Update(
            update_id=tg._update_id,
            message=Message(
                message_id=tg._update_id,
                date=datetime.now(),
                chat=CHAT,
                from_user=USER,
                successful_payment=SuccessfulPayment(
                    currency="XTR",
                    total_amount=amount,
                    invoice_payload=f"donate:{amount}",
                    telegram_payment_charge_id="charge-1",
                    provider_payment_charge_id="",
                ),
            ),
        )
    )


async def test_support_offers_amounts(tg: BotUnderTest):
    await tg.send("/support")

    assert "Підтримати трекер" in tg.texts[0]
    assert tg.buttons == ["50 ⭐", "100 ⭐", "250 ⭐"]


async def test_choosing_amount_sends_stars_invoice(tg: BotUnderTest):
    await tg.tap("support:100")

    # Кнопка підтверджена (без «годинника») і рахунок саме в Stars.
    assert tg.sent == [
        ("Alert", "", []),
        ("SendInvoice", "donate:100", ["100 XTR"]),
    ]


async def test_forged_amount_gets_no_invoice(tg: BotUnderTest):
    """callback_data шле клієнт — модифікований клієнт може вписати будь-що."""
    await tg.tap("support:1")

    assert [kind for kind, _, _ in tg.sent] == ["Alert"]
    assert "недоступна" in tg.texts[0]


async def test_pre_checkout_accepts_own_invoice(tg: BotUnderTest):
    await pre_checkout(tg, "donate:250", 250)

    assert tg.sent == [("PreCheckout", "", ["ok"])]


@pytest.mark.parametrize(
    ("payload", "amount", "currency"),
    [
        ("donate:250", 1, "XTR"),  # сума не збігається з payload
        ("donate:7", 7, "XTR"),  # суми немає в списку
        ("donate:100", 100, "USD"),  # не Stars
        ("pro:100", 100, "XTR"),  # чужий рахунок
        ("donate:abc", 100, "XTR"),  # сміття в payload
    ],
)
async def test_pre_checkout_rejects_suspicious(
    tg: BotUnderTest, payload: str, amount: int, currency: str
):
    """Відмова на цьому кроці — останній момент, коли гроші ще не списані."""
    await pre_checkout(tg, payload, amount, currency)

    assert [verdict for _, _, (verdict,) in tg.sent] == ["reject"]
    assert tg.sent[0][1], "відмова має пояснювати причину"


async def test_successful_payment_thanks(tg: BotUnderTest):
    await paid(tg, 100)

    assert tg.texts == ["Дякую за підтримку! 💛 Отримано 100 ⭐"]


async def test_payment_mid_dialog_still_thanks(tg: BotUnderTest):
    """Оплата посеред створення звички не має з'їстися діалогом.

    new_habit ловить у стані ВСЕ, що не текст («надішли назву текстом»).
    Якщо роутер донатів підключити після нього, людина замість подяки
    отримає пояснення про назву звички.
    """
    await tg.send("/new")
    await paid(tg, 50)

    assert tg.texts == ["Дякую за підтримку! 💛 Отримано 50 ⭐"]
    # І діалог не перервався — людина може спокійно дописати назву.
    assert await tg.fsm_state() is not None


async def test_paysupport_escapes_contact(tg: BotUnderTest, monkeypatch):
    monkeypatch.setattr(support, "SUPPORT_CONTACT", "<Розробник>")

    await tg.send("/paysupport")

    assert "&lt;Розробник&gt;" in tg.texts[0]


def test_main_registers_support_before_dialogs():
    """Порядок у фікстурі вище перевіряє лише тести; тут — бойовий бот."""
    import inspect

    import bot.__main__ as entry

    source = inspect.getsource(entry.main)
    assert source.index("support.router") < source.index("new_habit.router")
