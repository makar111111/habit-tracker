"""Керування звичками з бота: перейменувати, змінити опис, архів, видалити.

Окремий роутер від checkin.py навмисно, хоч обидва працюють із кнопками
звичок. Причина в тому, ЩО вони роблять: checkin — одна дія в один дотик,
без стану й без наслідків (натиснув не туди — натиснув ще раз). Тут же
діалоги з введенням тексту й незворотне видалення. Змішувати їх в одному
файлі означало б, що найнебезпечніший код лежить упритул до найбуденнішого.

Екрани та переходи між ними:

    список звичок ──⚙️ Керувати──> список для керування ──📦 Архів──> архів
                                          │                              │
                                    дотик по звичці                дотик по звичці
                                          ▼                              ▼
                                   картка звички ──🗑──>  підтвердження  <──🗑── архівна картка
                                    │     │     │              │                  │
                              ✏️ назва 📝 опис 📦 в архів   видалення        ♻️ відновити
                                    ▼     ▼
                                 введення тексту

Архів — пауза без втрати історії. Тому, на відміну від видалення, він
не питає підтвердження: помилку виправляє один дотик «Відновити».
"""

import asyncio
from collections import defaultdict
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.api import MAX_NAME_LENGTH, HabitsAPI
from bot.keyboards import DayCallback, HabitCallback, MenuCallback
from bot.views import (
    archive_view,
    delete_confirm_view,
    habit_card_view,
    habits_view,
    manage_view,
)

router = Router(name="manage")

# Та сама межа, що й при створенні звички (bot/new_habit.py) — щоб
# перейменування не могло зробити те, чого не дозволяє створення.
MAX_DESCRIPTION_LENGTH = 500

# Рядок, яким людина прибирає опис. Потрібен, бо порожнє повідомлення
# в Telegram надіслати неможливо: без домовленості про такий символ
# опис можна було б лише замінити, але не прибрати.
CLEAR_MARKER = "-"

GONE = "Цієї звички вже немає. Онови список: /habits"

STALE = "Ця кнопка вже неактуальна. Відкрий картку заново: /manage"

ASK_NAME = "Введи нову назву.\n\nНадішли /cancel, щоб лишити як було."
ASK_DESCRIPTION = (
    f"Введи новий опис.\n\n"
    f"Надішли «{CLEAR_MARKER}», щоб прибрати опис зовсім, "
    f"або /cancel, щоб лишити як було."
)

# Лок на пару (чат, користувач) — той самий захист від подвійного кліку,
# що й у new_habit.py: два майже одночасні натискання «Так, видалити»
# інакше пішли б у API обидва, і другий отримав би 404 на вже видалену
# звичку замість тихого "все вже зроблено".
_locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)


class EditHabit(StatesGroup):
    """Кроки редагування. У даних стану лежить habit_id — яку саме звичку
    редагуємо: сам стан цього не памʼятає, а кнопка вже давно натиснута."""

    name = State()
    description = State()


async def _show(callback: CallbackQuery, view: tuple[str, object] | None) -> None:
    """Перемалювати повідомлення під кнопкою, або сказати, що звички немає.

    View-функції повертають None, коли звичка зникла (видалена в браузері
    чи іншим вікном Telegram) — а це буденна ситуація для кнопки, яка
    висить у чаті. Тому не помилка, а спокійне пояснення.
    """
    if view is None:
        await callback.answer(GONE, show_alert=True)
        return

    text, keyboard = view
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    elif callback.message is not None:
        await callback.bot.send_message(
            callback.message.chat.id, text, reply_markup=keyboard
        )


# ---------- навігація між екранами ----------


@router.message(Command("manage"))
async def manage_by_command(message: Message, api: HabitsAPI) -> None:
    if message.from_user is None:
        return

    text, keyboard = await manage_view(api, message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(MenuCallback.filter(F.action == "manage"))
async def open_manage(callback: CallbackQuery, api: HabitsAPI) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    await _show(callback, await manage_view(api, callback.from_user.id))


@router.callback_query(MenuCallback.filter(F.action == "habits"))
async def back_to_habits(callback: CallbackQuery, api: HabitsAPI) -> None:
    """Повернення зі списку керування до звичайного списку з відмітками."""
    await callback.answer()
    if callback.from_user is None:
        return

    await _show(callback, await habits_view(api, callback.from_user.id))


@router.callback_query(HabitCallback.filter(F.action == "open"))
async def open_card(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI
) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    await _show(
        callback,
        await habit_card_view(api, callback.from_user.id, callback_data.habit_id),
    )


# ---------- архів ----------


@router.callback_query(MenuCallback.filter(F.action == "archive"))
async def open_archive(callback: CallbackQuery, api: HabitsAPI) -> None:
    await callback.answer()
    if callback.from_user is None:
        return

    await _show(callback, await archive_view(api, callback.from_user.id))


async def _set_archived(
    callback: CallbackQuery, habit_id: int, api: HabitsAPI, *, archived: bool
) -> str:
    """Спільна частина «в архів» і «відновити». Повертає текст підказки.

    Без блокування від подвійного кліку, на відміну від видалення: обидві
    дії ідемпотентні на боці API — другий PATCH із тим самим archived
    нічого не змінює (дата архіву не зсувається), тож і захищати нема від чого.
    update_habit кидає HabitGone, якщо звичку тим часом видалили, — її
    текст покаже on_error.
    """
    await api.update_habit(callback.from_user.id, habit_id, archived=archived)
    return "Перенесено в архів 📦" if archived else "Відновлено ♻️"


@router.callback_query(HabitCallback.filter(F.action == "archive"))
async def do_archive(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI
) -> None:
    """В архів — і назад до списку керування, де звички вже не видно.

    Повертаємо саме в список, а не лишаємо в картці: людина щойно
    прибрала звичку з очей, і саме цей результат вона має побачити.
    """
    if callback.from_user is None:
        return

    note = await _set_archived(callback, callback_data.habit_id, api, archived=True)
    view = await manage_view(api, callback.from_user.id)
    await callback.answer(note)
    await _show(callback, view)


@router.callback_query(HabitCallback.filter(F.action == "unarchive"))
async def do_unarchive(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI
) -> None:
    """Відновити — і показати картку вже активної звички з усіма діями."""
    if callback.from_user is None:
        return

    note = await _set_archived(callback, callback_data.habit_id, api, archived=False)
    view = await habit_card_view(api, callback.from_user.id, callback_data.habit_id)
    if view is None:
        await callback.answer(GONE, show_alert=True)
        return
    await callback.answer(note)
    await _show(callback, view)


# ---------- відмітка за вчора ----------


@router.callback_query(DayCallback.filter())
async def mark_past_day(
    callback: CallbackQuery, callback_data: DayCallback, api: HabitsAPI
) -> None:
    """Відмітити або зняти відмітку за день, зашитий у кнопку картки.

    Робимо саме те, що написано на кнопці, а не «перемикаємо»: якщо день
    тим часом відмітили у браузері, «Відмітити» не має його зняти. Після
    дії картку перемальовуємо з бази — там і видно, як є насправді.

    Перевірки «посеред діалогу» немає з тієї ж причини, що в нагадуваннях:
    перемальовується сама картка, а не список, тож хибного сигналу
    «діалог завершено» людина не отримує.
    """
    if callback.from_user is None:
        return

    try:
        day = date.fromisoformat(callback_data.day)
    except ValueError:
        # Дані кнопки шле клієнт — модифікований може вписати що завгодно.
        await callback.answer(STALE, show_alert=True)
        return

    telegram_id = callback.from_user.id
    habit_id = callback_data.habit_id

    # API приймає відмітку за БУДЬ-ЯКИЙ день, зокрема майбутній. Кнопка
    # картки пропонує лише вчора, тож сьогоднішній і пізніші дні можуть
    # прийти тільки від підробленого клієнта — і роздули б серії наперед.
    if day >= await api.today(telegram_id):
        await callback.answer(STALE, show_alert=True)
        return

    if callback_data.action == "mark":
        created = await api.check_in(telegram_id, habit_id, day)
        note = f"Відмічено за {day:%d.%m} ✅" if created else "Цей день уже відмічено"
    elif callback_data.action == "unmark":
        removed = await api.undo_check_in(telegram_id, habit_id, day)
        note = (
            f"Відмітку за {day:%d.%m} знято" if removed else "Відмітку вже було знято"
        )
    else:
        await callback.answer(STALE, show_alert=True)
        return

    # Дані для картки — ДО відповіді на callback, як у toggle_checkin: якщо
    # запит впаде, on_error відповість на натискання сам, одним викликом.
    view = await habit_card_view(api, telegram_id, habit_id)
    if view is None:
        await callback.answer(GONE, show_alert=True)
        return
    await callback.answer(note)
    await _show(callback, view)


# ---------- видалення ----------


@router.callback_query(HabitCallback.filter(F.action == "delete"))
async def ask_delete(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI
) -> None:
    """Показати підтвердження — саме видалення робить наступний обробник."""
    await callback.answer()
    if callback.from_user is None:
        return

    await _show(
        callback,
        await delete_confirm_view(api, callback.from_user.id, callback_data.habit_id),
    )


@router.callback_query(HabitCallback.filter(F.action == "confirm_delete"))
async def do_delete(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI
) -> None:
    await callback.answer()
    if callback.from_user is None or callback.message is None:
        return

    telegram_id = callback.from_user.id

    async with _locks[(callback.message.chat.id, telegram_id)]:
        # include_archived: видаляти можна й з архіву. Без нього архівна
        # звичка виглядала б «уже видаленою», і кнопка мовчки нічого б не робила.
        habits = await api.list_habits(telegram_id, include_archived=True)
        if not any(h["id"] == callback_data.habit_id for h in habits):
            # Уже видалено — найімовірніше, другим кліком по тій самій
            # кнопці. Це не помилка: результат саме той, якого людина
            # хотіла, тож просто показуємо оновлений список.
            await _show(callback, await manage_view(api, telegram_id))
            return

        await api.delete_habit(telegram_id, callback_data.habit_id)

    await _show(callback, await manage_view(api, telegram_id))


# ---------- редагування: вхід у діалог ----------


@router.callback_query(HabitCallback.filter(F.action == "rename"))
async def ask_rename(
    callback: CallbackQuery, callback_data: HabitCallback, state: FSMContext
) -> None:
    await callback.answer()

    # habit_id кладемо у стан: коли людина надішле текст, кнопки з
    # її id вже не буде під рукою — лишиться саме повідомлення.
    await state.update_data(habit_id=callback_data.habit_id)
    await state.set_state(EditHabit.name)

    if callback.message is not None:
        await callback.bot.send_message(callback.message.chat.id, ASK_NAME)


@router.callback_query(HabitCallback.filter(F.action == "describe"))
async def ask_describe(
    callback: CallbackQuery, callback_data: HabitCallback, state: FSMContext
) -> None:
    await callback.answer()

    await state.update_data(habit_id=callback_data.habit_id)
    await state.set_state(EditHabit.description)

    if callback.message is not None:
        await callback.bot.send_message(callback.message.chat.id, ASK_DESCRIPTION)


# ---------- редагування: вихід із діалогу ----------


@router.message(Command("cancel"), StateFilter(EditHabit))
async def cancel_edit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано. Нічого не змінено.")


@router.message(StateFilter(EditHabit), F.text.startswith("/"))
async def command_during_edit(message: Message) -> None:
    """Команда посеред введення — та сама пастка, що й у new_habit.py.

    Без цього обробника /habits, надіслане замість нової назви, стало б
    цією назвою: обробник нижче приймає будь-який текст, а роутер menu
    стоїть у черзі після цього.
    """
    await message.answer(
        "Спершу завершимо редагування.\nНадішли /cancel, якщо передумав."
    )


# ---------- редагування: збереження ----------


async def _apply(
    message: Message,
    state: FSMContext,
    api: HabitsAPI,
    *,
    name: str | None = None,
    description: str | None = None,
) -> None:
    """Зберегти зміну й показати оновлену картку.

    Стан чистимо ПІСЛЯ успішного запиту — якщо API впаде, людина
    лишиться в діалозі й зможе просто надіслати текст ще раз. Той самий
    порядок, що й у new_habit.finish(), і з тієї ж причини.
    """
    if message.from_user is None:
        return

    data = await state.get_data()
    habit_id = data.get("habit_id")
    if habit_id is None:
        await state.clear()
        await message.answer(GONE)
        return

    await api.update_habit(
        message.from_user.id, habit_id, name=name, description=description
    )
    await state.clear()

    view = await habit_card_view(api, message.from_user.id, habit_id)
    if view is None:
        await message.answer(GONE)
        return

    text, keyboard = view
    await message.answer(f"Збережено ✅\n\n{text}", reply_markup=keyboard)


@router.message(EditHabit.name, F.text)
async def got_new_name(message: Message, state: FSMContext, api: HabitsAPI) -> None:
    name = (message.text or "").strip()

    if not name:
        await message.answer("Назва не може бути порожньою. Спробуй ще раз.")
        return

    if len(name) > MAX_NAME_LENGTH:
        await message.answer(
            f"Задовга назва: {len(name)} символів, а можна до {MAX_NAME_LENGTH}."
        )
        return

    await _apply(message, state, api, name=name)


@router.message(EditHabit.name)
async def new_name_must_be_text(message: Message) -> None:
    await message.answer("Потрібен текст. Надішли нову назву словами.")


@router.message(EditHabit.description, F.text)
async def got_new_description(
    message: Message, state: FSMContext, api: HabitsAPI
) -> None:
    description = (message.text or "").strip()

    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"Задовгий опис: {len(description)} символів, "
            f"а можна до {MAX_DESCRIPTION_LENGTH}."
        )
        return

    # Домовлений символ означає «прибрати опис» — порожнє повідомлення
    # Telegram надіслати не дасть.
    if description == CLEAR_MARKER:
        description = ""

    await _apply(message, state, api, description=description)


@router.message(EditHabit.description)
async def new_description_must_be_text(message: Message) -> None:
    await message.answer(
        f"Потрібен текст. Надішли опис словами, «{CLEAR_MARKER}» щоб прибрати, "
        f"або /cancel."
    )
