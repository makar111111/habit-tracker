"""Створення звички діалогом: назва → опис → розклад.

Тут працює FSM — машина станів. Ідея проста: бот памʼятає, на якому
кроці розмови перебуває кожен співрозмовник. Без неї бот не відрізнив
би «Йога» як назву нової звички від «Йога» як випадкового повідомлення.

Стан у aiogram зберігається окремо для кожної пари (чат, користувач),
тож двоє людей можуть створювати звички одночасно, не заважаючи одне
одному — і навіть одна людина в різних чатах.
"""

import asyncio
from collections import defaultdict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.api import MAX_NAME_LENGTH, HabitsAPI
from bot.keyboards import (
    MenuCallback,
    ScheduleCallback,
    schedule_keyboard,
    skip_description_keyboard,
)
from bot.schedule import EVERY_DAY, WORKDAYS, schedule_label, toggle_day
from bot.views import habits_view

router = Router(name="new_habit")

# MAX_NAME_LENGTH приходить з bot/api.py — це межа, задана моделлю на
# боці API. Перевіряємо її тут, щоб людина отримала зрозуміле пояснення,
# а не сухе «422» від сервера.
#
# Опис на боці API не обмежений; це наша власна межа, щоб у базу не
# поїхала стаття на кілька сторінок.
MAX_DESCRIPTION_LENGTH = 500

ASK_NAME = "Як назвемо звичку?\n\nНадішли /cancel, щоб передумати."
ASK_DESCRIPTION = (
    "Тепер опис — навіщо вона тобі.\n\n"
    "Можна пропустити кнопкою нижче або надіслати /cancel."
)


class NewHabit(StatesGroup):
    """Кроки діалогу. Кожен State — це «чого бот зараз чекає»."""

    name = State()
    description = State()
    weekdays = State()


def schedule_prompt(weekdays: list[int]) -> str:
    """Питання про розклад із поточним вибором.

    Розклад окремим обов'язковим кроком, а не прихованою кнопкою: після
    створення його змінити не можна (так вирішено в специфікації, щоб
    редагування не переписувало минулі відсотки). Отже, створення — єдиний
    момент, коли людина взагалі може його обрати.
    """
    current = schedule_label(weekdays) if weekdays else "жодного дня"
    return (
        "Коли виконувати?\n\n"
        "Натискай дні, щоб увімкнути чи вимкнути. Розклад не змінюється "
        "після створення — так історія лишається точною.\n\n"
        f"Зараз: <b>{current}</b>"
    )


# ---------- вхід у діалог ----------


@router.message(Command("new"))
async def start_by_command(message: Message, state: FSMContext) -> None:
    await state.set_state(NewHabit.name)
    await message.answer(ASK_NAME)


@router.callback_query(MenuCallback.filter(F.action == "new_habit"))
async def start_by_button(callback: CallbackQuery, state: FSMContext) -> None:
    """Той самий діалог, але з кнопки «➕ Нова звичка»."""
    # Підтвердження кнопки — завжди першим рядком, до всіх перевірок
    # і мережевих викликів. Так «годинник» знімається навіть якщо далі
    # щось піде не так.
    await callback.answer()
    await state.set_state(NewHabit.name)

    if callback.message is not None:
        await callback.bot.send_message(callback.message.chat.id, ASK_NAME)


# ---------- вихід із діалогу ----------


@router.message(Command("cancel"), StateFilter(NewHabit))
async def cancel(message: Message, state: FSMContext) -> None:
    """Перервати створення.

    StateFilter(NewHabit) означає «лише всередині цього діалогу»:
    /cancel посеред звичайної розмови нічого не скасовує й не бентежить
    людину повідомленням про скасування того, чого вона не починала.
    """
    await state.clear()
    await message.answer("Скасовано. Нічого не створено.")


@router.message(StateFilter(NewHabit), F.text.startswith("/"))
async def command_during_dialog(message: Message) -> None:
    """Команда, надіслана посеред діалогу.

    Без цього обробника було б неприємно: роутер new_habit підключений
    раніше за menu, а обробник назви приймає БУДЬ-ЯКИЙ текст. Тому
    людина, що написала /habits, не побачила б список — вона створила б
    звичку з назвою «/habits» і навіть не зрозуміла б, звідки та взялась.

    Стоїть саме тут, після /cancel: той зареєстрований вище, тож
    скасувати діалог і далі можна.
    """
    await message.answer(
        "Спершу завершимо створення звички.\nНадішли /cancel, якщо передумав."
    )


# ---------- крок 1: назва ----------


@router.message(NewHabit.name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    """Обробник спрацьовує ЛИШЕ коли бот чекає назву.

    Перший аргумент фільтра — стан. Саме він відрізняє «Йога» як
    відповідь на питання від «Йога», написаного просто так.
    """
    name = (message.text or "").strip()

    if not name:
        await message.answer("Назва не може бути порожньою. Спробуй ще раз.")
        return

    if len(name) > MAX_NAME_LENGTH:
        await message.answer(
            f"Задовга назва: {len(name)} символів, а можна до {MAX_NAME_LENGTH}."
        )
        return

    # update_data кладе значення у сховище стану — воно переживе
    # перехід на наступний крок і дочекається там своєї черги.
    await state.update_data(name=name)
    await state.set_state(NewHabit.description)
    await message.answer(ASK_DESCRIPTION, reply_markup=skip_description_keyboard())


@router.message(NewHabit.name)
async def name_must_be_text(message: Message) -> None:
    """Стікер чи фото замість назви.

    Без цього обробника бот на стікер просто мовчав би, і людина не
    зрозуміла б, чи він завис, чи чекає чогось іншого.
    """
    await message.answer("Потрібен текст. Надішли назву звички словами.")


# ---------- крок 2: опис ----------


LOST_STATE = (
    "Цю звичку вже або створено, або діалог перервався. Перевір список: /habits"
)

# Лок на кожну пару (chat_id, telegram_id). Навіщо: без нього подвійний
# клік «Пропустити» (миша, тремтяча рука, нетерплячий палець) запускає
# ДВА майже одночасні виклики finish(). Обидва встигають прочитати той
# самий стан ДО того, як перший його прибере — і тоді або звичка
# створюється двічі, або другий виклик бачить порожні дані й показує
# людині "діалог загубився", хоча насправді все щойно вдалося. Лок
# серіалізує їх: другий викликач дочекається першого і застане стан
# уже прибраним — чесно, без гонки.
_dialog_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)


async def ask_schedule(
    state: FSMContext, chat_id: int, telegram_id: int, description: str
) -> bool:
    """Запам'ятати опис і перейти до розкладу.

    False — якщо на кроці опису нас уже немає: це подвійний клік
    «Пропустити», і перший виклик уже показав питання про розклад.
    Без перевірки під локом людина отримала б два однакові повідомлення.
    """
    async with _dialog_locks[(chat_id, telegram_id)]:
        if await state.get_state() != NewHabit.description.state:
            return False
        await state.update_data(description=description, weekdays=EVERY_DAY)
        await state.set_state(NewHabit.weekdays)
        return True


async def finish(
    api: HabitsAPI,
    state: FSMContext,
    chat_id: int,
    telegram_id: int,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Створити звичку й прибрати за собою стан.

    Повертає None, якщо на момент виклику зберігати вже нічого —
    або стан справді загубився (бот перезапустився посеред діалогу),
    або нас випередив паралельний виклик (той самий подвійний клік).
    LOST_STATE сформульовано так, щоб бути правдою в обох випадках.
    """
    async with _dialog_locks[(chat_id, telegram_id)]:
        data = await state.get_data()
        name = data.get("name")
        if not name:
            return None

        # Спершу створюємо звичку і ЛИШЕ ПОТІМ чистимо стан. Порядок тут
        # важливий: якщо create_habit впаде (API ліг, обрив мережі),
        # стан лишиться на місці, і порада «спробуй ще раз» справді
        # спрацює. У зворотному порядку збій губив би назву й опис
        # безповоротно разом зі станом — а «спробуй ще раз» виконати
        # було б неможливо: наступне повідомлення падало б у порожнечу.
        await api.create_habit(
            telegram_id,
            name,
            data.get("description", ""),
            weekdays=data.get("weekdays", EVERY_DAY),
        )
        await state.clear()
        return await habits_view(api, telegram_id)


@router.message(NewHabit.description, F.text)
async def got_description(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    description = (message.text or "").strip()

    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"Задовгий опис: {len(description)} символів, "
            f"а можна до {MAX_DESCRIPTION_LENGTH}."
        )
        return

    if await ask_schedule(state, message.chat.id, message.from_user.id, description):
        await message.answer(
            schedule_prompt(EVERY_DAY), reply_markup=schedule_keyboard(EVERY_DAY)
        )


@router.message(NewHabit.description)
async def description_must_be_text(message: Message) -> None:
    """Стікер, фото чи голосове замість опису.

    Дзеркало до name_must_be_text із кроку назви. Без цього обробника
    бот на кроці опису мовчав би зовсім: got_description вимагає F.text,
    command_during_dialog — F.text.startswith("/"), а решта роутерів
    message-подій без команд не ловить. Оновлення тонуло б без сліду,
    а людина лишалася б у стані NewHabit.description, гадаючи, чи бот
    ще живий. Той самий принцип, що вже застосований до стікера на
    кроці назви й до «нічийної» кнопки в checkin.py: мовчання виглядає
    як зависання, навіть коли бот насправді просто чекає іншого вводу.
    """
    await message.answer(
        "Потрібен текст. Надішли опис словами, натисни «Пропустити» або /cancel."
    )


@router.callback_query(
    NewHabit.description, MenuCallback.filter(F.action == "skip_description")
)
async def skipped_description(callback: CallbackQuery, state: FSMContext) -> None:
    # Підтверджуємо натискання ПЕРШИМ ділом, ще до будь-яких перевірок.
    # Інакше на гілці з return кнопка лишилася б із «годинником»
    # до самого таймауту — бот виглядав би зависшим.
    await callback.answer()

    if callback.from_user is None or callback.message is None:
        return

    if await ask_schedule(state, callback.message.chat.id, callback.from_user.id, ""):
        await callback.bot.send_message(
            callback.message.chat.id,
            schedule_prompt(EVERY_DAY),
            reply_markup=schedule_keyboard(EVERY_DAY),
        )


# ---------- крок 3: розклад ----------


@router.callback_query(
    NewHabit.weekdays,
    ScheduleCallback.filter(F.action.in_({"toggle", "daily", "workdays"})),
)
async def changed_schedule(
    callback: CallbackQuery, callback_data: ScheduleCallback, state: FSMContext
) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return
    # Дані кнопки приходять від клієнта Telegram, і змінений клієнт може
    # надіслати будь-що. День 99 інакше тихо дожив би до API й упав там 422.
    if callback_data.action == "toggle" and not 0 <= callback_data.day <= 6:
        return

    # Під тим самим локом, що й створення: два швидкі дотики до різних днів
    # читали б один і той самий стан, і другий затер би зміну першого.
    # Редагування повідомлення теж усередині — інакше відповіді могли б
    # прийти в Telegram у зворотному порядку й показати застарілий вибір.
    async with _dialog_locks[(callback.message.chat.id, callback.from_user.id)]:
        current = (await state.get_data()).get("weekdays", EVERY_DAY)
        if callback_data.action == "toggle":
            chosen = toggle_day(current, callback_data.day)
        elif callback_data.action == "daily":
            chosen = EVERY_DAY
        else:
            chosen = WORKDAYS
        if chosen == current:
            return
        await state.update_data(weekdays=chosen)

        if isinstance(callback.message, Message):
            try:
                await callback.message.edit_text(
                    schedule_prompt(chosen), reply_markup=schedule_keyboard(chosen)
                )
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error):
                    raise


@router.callback_query(NewHabit.weekdays, ScheduleCallback.filter(F.action == "done"))
async def schedule_done(
    callback: CallbackQuery, state: FSMContext, api: HabitsAPI
) -> None:
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return

    # Відповісти на натискання можна лише один раз, тому спершу вирішуємо,
    # що сказати. Читання стану з пам'яті миттєве — «годинник» не встигне.
    if not (await state.get_data()).get("weekdays"):
        await callback.answer("Обери хоча б один день.", show_alert=True)
        return
    await callback.answer()

    result = await finish(api, state, callback.message.chat.id, callback.from_user.id)
    if result is None:
        await callback.bot.send_message(callback.message.chat.id, LOST_STATE)
        return

    text, keyboard = result
    await callback.bot.send_message(
        callback.message.chat.id, f"Готово! ✅\n\n{text}", reply_markup=keyboard
    )


@router.message(NewHabit.weekdays)
async def schedule_expects_buttons(message: Message) -> None:
    """Текст замість кнопок. Мовчання тут виглядало б як зависання."""
    await message.answer(
        "Обери дні кнопками вище й натисни «Створити звичку». Або /cancel."
    )
