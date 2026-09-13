"""Клавіатури під повідомленнями бота.

Інлайн-кнопки замість команд — це не прикраса. Команду /done 3 треба
памʼятати й знати номер звички; кнопку просто видно й у неї тицяєш.
Різниця між «навчальним ботом» і тим, яким справді користуються.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.schedule import WEEKDAY_NAMES

# Telegram обмежує підпис кнопки. Назва звички може бути до 100 символів,
# тож довгу доведеться вкоротити, інакше API відхилить усе повідомлення.
MAX_BUTTON_TEXT = 40


class HabitCallback(CallbackData, prefix="habit"):
    """Що зашито в кнопку звички.

    Поле action розрізняє, ЩО саме робить кнопка з цією звичкою:
    "toggle" — відмітити/зняти, "open" — відкрити картку керування,
    "rename" / "describe" — почати редагування, "delete" — спитати
    підтвердження, "confirm_delete" — видалити насправді.

    Замість того, щоб ліпити рядок "habit:toggle:3" і потім розбирати
    його вручну, описуємо поля класом. aiogram сам збере рядок при
    створенні кнопки й сам розбере назад при натисканні — з перевіркою
    типів. Це прибирає цілий клас помилок: помилився в форматі —
    дізнаєшся одразу, а не від користувача через тиждень.

    Важливо: Telegram дає на всі дані кнопки лише 64 байти,
    тому поля мають бути короткі. Тут це префікс, дія та число.
    """

    action: str
    habit_id: int


class MenuCallback(CallbackData, prefix="menu"):
    """Кнопки, не привʼязані до конкретної звички."""

    action: str


class ScheduleCallback(CallbackData, prefix="sched"):
    """Кнопки кроку розкладу: "toggle" з номером дня, "daily", "workdays", "done".

    day має значення лише для "toggle"; для решти дій лишається 0.
    """

    action: str
    day: int = 0


def shorten(text: str) -> str:
    """Вкоротити назву, щоб влізла в кнопку.

    Просте text[:N] могло б розрізати навпіл прапор: у Unicode прапор —
    не один символ, а ПАРА regional-indicator символів (наприклад,
    🇺 + 🇦 = 🇺🇦). Обрив рівно між ними лишає на кнопці не половину
    прапора, а самотню літеру в квадратній рамці — переконались на
    назві з полусотнею прапорів поспіль. Тому відступаємо ще на один
    символ назад, якщо різ припадає на непарну кількість таких символів.

    Це не рятує від УСІХ складних emoji-послідовностей — родина чи
    певний відтінок шкіри клеїться кількома символами через невидимий
    ZWJ, і повний захист від такого вимагав би розбиття на графемні
    кластери (пакет regex, UAX #29). Для назви звички, яку людина
    друкує сама, прицільний фікс під найпоширеніший випадок — прапори —
    досить, а тягнути нову залежність заради рідкісного краю сенсу нема.
    """
    if len(text) <= MAX_BUTTON_TEXT:
        return text

    # Обрізаємо з запасом на три крапки, щоб підсумок точно вліз у ліміт.
    cut = MAX_BUTTON_TEXT - 1

    trailing_regional_indicators = 0
    i = cut - 1
    while i >= 0 and 0x1F1E6 <= ord(text[i]) <= 0x1F1FF:
        trailing_regional_indicators += 1
        i -= 1
    if trailing_regional_indicators % 2 == 1:
        cut -= 1

    return text[:cut].rstrip() + "…"


def habit_button_text(habit: dict) -> str:
    """Підпис кнопки: стан на сьогодні, назва і довжина серії.

    Приклад:  ✅ Зарядка · 🔥 7
    """
    stats = habit.get("stats") or {}

    if stats.get("done_today"):
        mark = "✅"
    else:
        mark = "⬜" if habit.get("due_today", True) else "💤"
    label = f"{mark} {shorten(habit['name'])}"

    # Серію показуємо лише коли вона є. Нулик біля кожної звички
    # виглядав би як докір, а не як інформація.
    streak = stats.get("current_streak") or 0
    if streak:
        label += f" · 🔥 {streak}"

    return label


def habits_keyboard(habits: list[dict]) -> InlineKeyboardMarkup:
    """Список звичок кнопками: одна звичка — один рядок."""
    builder = InlineKeyboardBuilder()

    for habit in habits:
        builder.button(
            text=habit_button_text(habit),
            # Об'єкт, а не рядок. У рядок його перетворить сам aiogram.
            callback_data=HabitCallback(action="toggle", habit_id=habit["id"]),
        )

    builder.button(
        text="➕ Нова звичка", callback_data=MenuCallback(action="new_habit")
    )
    builder.button(text="⚙️ Керувати", callback_data=MenuCallback(action="manage"))

    # adjust(1) — по одній кнопці в рядок. Без цього aiogram спробував би
    # скласти їх по кілька, і довгі назви перетворилися б на кашу.
    builder.adjust(1)
    return builder.as_markup()


def manage_keyboard(habits: list[dict]) -> InlineKeyboardMarkup:
    """Список звичок у режимі керування: дотик відкриває картку.

    Навмисно без позначок ✅/⬜ і серій: тут людина не відмічає, а
    порядкує. Однакові на вигляд кнопки, що роблять різне, — найкоротший
    шлях до випадкового натискання не туди.
    """
    builder = InlineKeyboardBuilder()

    for habit in habits:
        builder.button(
            text=shorten(habit["name"]),
            callback_data=HabitCallback(action="open", habit_id=habit["id"]),
        )

    builder.button(text="⬅️ До списку", callback_data=MenuCallback(action="habits"))
    builder.adjust(1)
    return builder.as_markup()


def habit_card_keyboard(habit_id: int) -> InlineKeyboardMarkup:
    """Кнопки під карткою однієї звички."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✏️ Перейменувати",
        callback_data=HabitCallback(action="rename", habit_id=habit_id),
    )
    builder.button(
        text="📝 Змінити опис",
        callback_data=HabitCallback(action="describe", habit_id=habit_id),
    )
    builder.button(
        text="🗑 Видалити",
        callback_data=HabitCallback(action="delete", habit_id=habit_id),
    )
    builder.button(text="⬅️ Назад", callback_data=MenuCallback(action="manage"))

    # Дві кнопки редагування поруч, а видалення — окремим рядком:
    # найнебезпечніша дія не має стояти впритул до буденних.
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def delete_confirm_keyboard(habit_id: int) -> InlineKeyboardMarkup:
    """Підтвердження видалення.

    Окремий екран, а не одразу видалення: разом зі звичкою зникають
    усі її відмітки, іноді за місяці. Скасувати це неможливо, тож
    зайвий дотик тут — не тяганина, а страховка.
    """
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🗑 Так, видалити",
        callback_data=HabitCallback(action="confirm_delete", habit_id=habit_id),
    )
    # «Скасувати» веде назад у картку тієї самої звички — людина
    # опиняється там, звідки прийшла.
    builder.button(
        text="⬅️ Скасувати",
        callback_data=HabitCallback(action="open", habit_id=habit_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def skip_description_keyboard() -> InlineKeyboardMarkup:
    """Одна кнопка «пропустити» для кроку з описом."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Пропустити", callback_data=MenuCallback(action="skip_description")
    )
    return builder.as_markup()


def schedule_keyboard(weekdays: list[int]) -> InlineKeyboardMarkup:
    """Дні тижня перемикачами, дві швидкі кнопки й «Створити».

    Дні — у два ряди (4 + 3): сім кнопок з позначкою в один ряд на
    телефоні обрізаються до «✅ П…», і Пн уже не відрізнити від Пт.
    """
    builder = InlineKeyboardBuilder()
    for day, name in enumerate(WEEKDAY_NAMES):
        mark = "✅" if day in weekdays else "▫️"
        builder.button(text=f"{mark} {name}", callback_data=ScheduleCallback(action="toggle", day=day))
    builder.button(text="Щодня", callback_data=ScheduleCallback(action="daily"))
    builder.button(text="Будні", callback_data=ScheduleCallback(action="workdays"))
    builder.button(text="Створити звичку", callback_data=ScheduleCallback(action="done"))
    builder.adjust(4, 3, 2, 1)
    return builder.as_markup()
