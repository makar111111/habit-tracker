"""Тести клієнта бота проти справжнього застосунку.

Головна ідея: сервер не запускається. httpx уміє звертатися до
ASGI-застосунку напряму, в тому ж процесі — через ASGITransport.
Мережі немає, портів немає, а код застосунку виконується справжній.

Чому це краще, ніж підсунути боту вигадані відповіді (моки): мок
підтверджує лише те, що ми правильно вгадали формат. Якщо у відповіді
API перейменують поле, мок і далі радісно зеленітиме, а бот у Telegram
зламається. Тут же перевіряється справжній контракт між двома частинами.
"""

import asyncio
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

import auth
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.new_habit import finish
from bot.api import MAX_NAME_LENGTH, ApiError, HabitGone, HabitsAPI
from conftest import TEST_BOT_SECRET
from database import get_session
from main import app

OLENA = 111
IHOR = 222

# Усі тести цього файлу асинхронні — позначаємо весь модуль одразу,
# щоб не ставити @pytest.mark.asyncio над кожною функцією.
pytestmark = pytest.mark.asyncio


@pytest.fixture(name="api")
async def api_fixture(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Клієнт бота, підключений до застосунку з тимчасовою базою.

    Тут є одна важлива відмінність від фікстур в інших тестових файлах:
    база лежить у ФАЙЛІ, і сесія створюється на КОЖЕН запит — точно так,
    як у бойовому database.get_session.

    Це не педантизм. Метод habits_with_stats робить два запити одночасно
    через asyncio.gather, а одна спільна на всіх сесія SQLite такого не
    переживає: два потоки лізуть в одне зʼєднання й отримують
    «bad parameter or other API misuse». З попереднім варіантом фікстури
    (спільна сесія, база в памʼяті) тест падав приблизно раз на чотири
    прогони — випадково, а тому особливо неприємно.

    Інші тестові файли ходять у застосунок синхронним TestClient,
    по одному запиту за раз, тож їм спільної сесії цілком достатньо.
    """
    engine = create_engine(
        # as_posix(), бо у SQLAlchemy-адресі мають бути прямі слеші,
        # а на Windows tmp_path дає зворотні.
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    monkeypatch.setattr(auth, "BOT_SECRET", TEST_BOT_SECRET)

    api = HabitsAPI(secret=TEST_BOT_SECRET)

    # Підміняємо транспорт: замість справжніх HTTP-запитів у мережу
    # httpx викликатиме застосунок напряму. Для коду HabitsAPI
    # нічого не змінюється — він так само робить await на запит.
    await api._client.aclose()
    api._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield api

    await api.close()
    app.dependency_overrides.clear()
    engine.dispose()


# ---------- звички ----------


async def test_create_and_list(api: HabitsAPI):
    await api.create_habit(OLENA, "Йога", "щоранку")

    habits = await api.list_habits(OLENA)

    assert len(habits) == 1
    assert habits[0]["name"] == "Йога"
    assert habits[0]["description"] == "щоранку"


async def test_habits_with_stats_joins_both_endpoints(api: HabitsAPI):
    """Перевіряємо саме склейку: назва з /habits, показники з /stats."""
    habit = await api.create_habit(OLENA, "Йога")
    await api.check_in(OLENA, habit["id"])

    habits = await api.habits_with_stats(OLENA)

    assert habits[0]["name"] == "Йога"
    assert habits[0]["stats"]["done_today"] is True
    assert habits[0]["stats"]["current_streak"] == 1


async def test_habits_with_stats_works_without_checkins(api: HabitsAPI):
    """Звичка без жодної відмітки не має ламати склейку."""
    await api.create_habit(OLENA, "Йога")

    habits = await api.habits_with_stats(OLENA)

    assert habits[0]["stats"]["done_today"] is False
    assert habits[0]["stats"]["total"] == 0


# ---------- відмітки ----------


async def test_check_in_returns_true_then_false(api: HabitsAPI):
    """Головна перевірка перекладу 409 у побутовий сенс.

    Перший дотик створює відмітку, другий отримує від API 409 —
    і клієнт має віддати False, а не кинути виняток. На цьому
    тримається перемикач у checkin.py.
    """
    habit = await api.create_habit(OLENA, "Йога")

    assert await api.check_in(OLENA, habit["id"]) is True
    assert await api.check_in(OLENA, habit["id"]) is False


async def test_undo_check_in(api: HabitsAPI):
    habit = await api.create_habit(OLENA, "Йога")
    await api.check_in(OLENA, habit["id"])

    assert await api.undo_check_in(OLENA, habit["id"], date.today()) is True

    habits = await api.habits_with_stats(OLENA)
    assert habits[0]["stats"]["done_today"] is False


async def test_undo_without_checkin_returns_false(api: HabitsAPI):
    """Знімати нічого — не помилка, просто False."""
    habit = await api.create_habit(OLENA, "Йога")

    assert await api.undo_check_in(OLENA, habit["id"], date.today()) is False


async def test_undo_specific_day(api: HabitsAPI):
    habit = await api.create_habit(OLENA, "Йога")
    await api.check_in(OLENA, habit["id"])

    yesterday = date.today() - timedelta(days=1)

    # Учора не відмічали — знімати нічого.
    assert await api.undo_check_in(OLENA, habit["id"], yesterday) is False
    # А сьогоднішня відмітка має вціліти.
    assert await api.undo_check_in(OLENA, habit["id"], date.today()) is True


# ---------- користувач ----------


async def test_set_cyrillic_name(api: HabitsAPI):
    """Імʼя з кирилицею має доїхати цілим.

    Саме заради цього воно передається тілом JSON, а не заголовком.
    """
    await api.set_name(OLENA, "Олена Ковальчук")

    response = await api._request("GET", "/users/me", OLENA)
    assert response.json()["name"] == "Олена Ковальчук"


# ---------- ізоляція ----------


async def test_users_are_isolated(api: HabitsAPI):
    """Той самий клієнт бота обслуговує різних людей окремо."""
    await api.create_habit(OLENA, "Йога")
    await api.create_habit(IHOR, "Біг")

    olena = await api.list_habits(OLENA)
    ihor = await api.list_habits(IHOR)

    assert [h["name"] for h in olena] == ["Йога"]
    assert [h["name"] for h in ihor] == ["Біг"]


async def test_cannot_touch_someone_elses_habit(api: HabitsAPI):
    """Чужа звичка недосяжна навіть якщо знаєш її id."""
    habit = await api.create_habit(OLENA, "Йога")

    with pytest.raises(ApiError):
        await api.check_in(IHOR, habit["id"])


# ---------- налаштування ----------


async def test_wrong_secret_gives_clear_message(api: HabitsAPI):
    """Найчастіша помилка налаштування має пояснювати себе сама."""
    # Латиницею: кирилиця в HTTP-заголовок не влазить у принципі.
    api._secret = "wrong-secret"

    with pytest.raises(ApiError, match="BOT_SECRET"):
        await api.list_habits(OLENA)


async def test_set_name_clamps_too_long_name(api: HabitsAPI):
    """Довге імʼя з Telegram не має ламати /start.

    Telegram дозволяє по 64 символи на імʼя та прізвище, тож full_name
    сягає 129 — а модель на боці API приймає 100. Без підрізання людина
    з довгим імʼям бачила б замість привітання дамп помилки валідації,
    і так на КОЖЕН /start.
    """
    long_name = "Я" * 64 + " " + "К" * 64
    assert len(long_name) > MAX_NAME_LENGTH

    await api.set_name(OLENA, long_name)

    response = await api._request("GET", "/users/me", OLENA)
    assert len(response.json()["name"]) == MAX_NAME_LENGTH


async def test_check_in_on_deleted_habit_says_it_plainly(api: HabitsAPI):
    """Кнопка звички, яку видалили у браузері, має пояснити себе.

    Тут 404 однозначний — відмітки ще не існує, тож ідеться саме про
    звичку. Тому його можна безпечно перекласти в людський текст.
    """
    habit = await api.create_habit(OLENA, "Йога")
    await api.delete_habit(OLENA, habit["id"])

    with pytest.raises(HabitGone, match="вже немає"):
        await api.check_in(OLENA, habit["id"])


async def test_undo_on_deleted_habit_returns_false(api: HabitsAPI):
    """А от при знятті відмітки 404 неоднозначний — і тому НЕ виняток.

    Сервер віддає той самий код і коли немає звички, і коли немає
    відмітки. Другий випадок буденний, тож обидва зводяться до False:
    екран усе одно перемальовується з бази й покаже правду.
    """
    habit = await api.create_habit(OLENA, "Йога")
    await api.delete_habit(OLENA, habit["id"])

    assert await api.undo_check_in(OLENA, habit["id"], date.today()) is False


# ---------- діалог створення звички ----------


def make_state() -> FSMContext:
    """Порожній стан FSM, як у щойно початого діалогу."""
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=1, user_id=OLENA),
    )


async def test_finish_creates_habit_and_clears_state(api: HabitsAPI):
    state = make_state()
    await state.update_data(name="Йога")

    result = await finish(api, state, 1, OLENA, "щоранку")

    assert result is not None
    text, keyboard = result
    assert "Йога" in [b.text.split(" ", 1)[-1] for row in keyboard.inline_keyboard for b in row]

    habits = await api.list_habits(OLENA)
    assert habits[0]["name"] == "Йога"
    assert habits[0]["description"] == "щоранку"

    # Стан має бути прибраний, інакше наступне повідомлення людини
    # знову сприймалося б як частина діалогу.
    assert await state.get_data() == {}


async def test_finish_returns_none_when_name_lost(api: HabitsAPI):
    """Якщо стан загубився, finish має віддати None — і НЕ впасти.

    Тест не теоретичний: колись finish повертав None, а обидва виклики
    робили `text, keyboard = await finish(...)`. Захист від KeyError
    перетворювався на TypeError під час розпакування — тобто робив
    гірше, ніж якби його не було.
    """
    state = make_state()  # даних немає

    assert await finish(api, state, 1, OLENA, "") is None


async def test_finish_preserves_state_when_create_fails(
    api: HabitsAPI, monkeypatch: pytest.MonkeyPatch
):
    """Якщо create_habit падає, стан має ЛИШИТИСЬ — щоб "спробуй ще раз" спрацював.

    Раніше finish() чистив стан ДО create_habit. Збій API стирав назву
    й опис безповоротно разом зі станом, а порада "спробуй ще раз" з
    on_error була нездійсненна: наступне повідомлення падало в порожнечу
    (стан None не підходить жодному обробнику).
    """
    state = make_state()
    await state.update_data(name="Зарядка")

    async def failing(*args, **kwargs):
        from bot.api import ApiUnavailable
        raise ApiUnavailable("симуляція обриву")

    monkeypatch.setattr(api, "create_habit", failing)

    with pytest.raises(Exception):
        await finish(api, state, 1, OLENA, "опис")

    # Стан і дані мають вціліти — саме це дає змогу повторити спробу.
    assert await state.get_data() == {"name": "Зарядка"}
    assert await api.list_habits(OLENA) == []

    # І головне: жодної звички при цьому не створено.
    assert await api.list_habits(OLENA) == []


async def test_parallel_requests_do_not_corrupt_each_other(api: HabitsAPI):
    """Одночасні запити мають виживати — саме на це розрахований gather.

    Тест сторожить не бот, а припущення під ним: кожен HTTP-запит
    отримує власну сесію бази. Якщо колись хтось вирішить «зекономити»
    і зробить одну сесію на всіх, цей тест впаде — бо habits_with_stats
    ходить у /habits і /stats ОДНОЧАСНО.
    """
    for number in range(3):
        await api.create_habit(OLENA, f"Звичка {number}")

    results = await asyncio.gather(
        *[api.habits_with_stats(OLENA) for _ in range(20)]
    )

    assert all(len(r) == 3 for r in results)
