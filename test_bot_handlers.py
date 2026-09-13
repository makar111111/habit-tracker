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
from datetime import date, datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

import auth
import calendar_rules
from bot import checkin, manage, menu, new_habit
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
                    photo=[PhotoSize(file_id="f", file_unique_id="fu", width=10, height=10)],
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
    for router in (new_habit.router, manage.router, menu.router, checkin.router):
        router._parent_router = None


async def create_habit_via_dialog(tg: BotUnderTest, name: str) -> int:
    """Пройти діалог створення до кінця й повернути id звички."""
    await tg.send("/new")
    await tg.send(name)
    await tg.tap("menu:skip_description")

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
    assert "Готово" in tg.texts[-1]
    assert "⬜ Зарядка" in tg.buttons

    habits = await tg.api.list_habits(USER.id)
    assert [h["name"] for h in habits] == ["Зарядка"]


async def test_description_is_saved(tg: BotUnderTest):
    await tg.send("/new")
    await tg.send("Читати")
    await tg.send("20 хвилин перед сном")

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


async def test_undo_uses_owner_day_when_bot_clock_is_on_previous_day(tg: BotUnderTest, monkeypatch):
    class HostDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 12)

    monkeypatch.setattr(checkin, "date", HostDate, raising=False)
    monkeypatch.setattr(calendar_rules, "now_utc",
                        lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc))
    habit = await tg.api.create_habit(USER.id, "Йога")
    await tg.api.check_in(USER.id, habit["id"])
    previous = await tg.api._request("POST", f"/habits/{habit['id']}/checkins", USER.id,
                                     json={"day": "2026-09-12"})
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


async def test_habits_list_counts_planned_habits_and_labels_rest_days(tg: BotUnderTest, monkeypatch):
    monkeypatch.setattr(calendar_rules, "now_utc",
                        lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc))
    await tg.api.create_habit(USER.id, "Щодня")
    for name, fields in [
        ("Відпочинок", {"weekdays": [5]}),
        ("Архів", {}),
    ]:
        response = await tg.api._request("POST", "/habits", USER.id,
                                         json={"name": name, **fields})
        assert response.status_code == 201, response.text
        if name == "Архів":
            response = await tg.api._request("PATCH", f"/habits/{response.json()['id']}",
                                             USER.id, json={"archived": True})
            assert response.status_code == 200, response.text

    await tg.send("/habits")

    assert "Сьогодні відмічено: 0 з 1" in tg.texts[0]
    assert "не заплановано" in tg.texts[0]
    assert "⬜ Щодня" in tg.buttons
    assert "💤 Відпочинок" in tg.buttons
    assert all("Архів" not in button for button in tg.buttons)


async def test_habit_card_explains_rest_day(tg: BotUnderTest):
    today = await tg.api.today(USER.id)
    response = await tg.api._request("POST", "/habits", USER.id,
                                     json={"name": "Йога", "weekdays": [(today.weekday() + 1) % 7]})
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
    u1, u2 = tg.make_tap(f"habit:toggle:{habit_id}"), tg.make_tap(f"habit:toggle:{habit_id}")
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    alerts = [t for k, t, *_ in tg.sent if k == "Alert"]
    assert not any("не так" in a or "500" in a for a in alerts), (
        f"подвійний тап мав дати чисте відмітити+зняти, а не збій: {alerts}"
    )

    stats = next(
        h["stats"] for h in await tg.api.habits_with_stats(USER.id)
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
        h["stats"] for h in await tg.api.habits_with_stats(USER.id)
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


async def test_double_click_skip_creates_exactly_one_habit(tg: BotUnderTest):
    """Два майже одночасні кліки «Пропустити» — рівно одна звичка.

    Раніше два виклики finish() могли обидва прочитати той самий стан
    ДО того, як перший його прибере: другий бачив порожні дані й казав
    людині «діалог загубився», хоча перший щойно все створив успішно.
    Лок у new_habit.py серіалізує виклики — другий чекає на першого
    і застає стан уже прибраним, без гонки.
    """
    await tg.send("/new")
    await tg.send("Читати")

    tg.bot.session.sent.clear()
    u1 = tg.make_tap("menu:skip_description")
    u2 = tg.make_tap("menu:skip_description")
    await asyncio.gather(tg.feed(u1), tg.feed(u2))

    habits = await tg.api.list_habits(USER.id)
    assert [h["name"] for h in habits] == ["Читати"], "мала створитись рівно одна звичка"

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
                message_id=999, date=datetime.now(), chat=Chat(id=5555, type="private"),
                from_user=weird_user, text="/start",
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
        h["stats"] for h in await tg.api.habits_with_stats(USER.id)
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
