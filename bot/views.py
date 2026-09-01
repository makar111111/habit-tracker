"""Складання екрана зі списком звичок.

Винесено окремо, бо цей самий екран малюють двоє: команда /habits
(створює нове повідомлення) і натискання кнопки (перемальовує старе).
Якби код лежав в одному з них, другий мусив би його імпортувати —
і вийшов би заплутаний звʼязок між обробниками.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.text_decorations import html_decoration

from bot.api import HabitsAPI
from bot.keyboards import (
    delete_confirm_keyboard,
    habit_card_keyboard,
    habits_keyboard,
    manage_keyboard,
)
from bot.plural import plural

EMPTY_TEXT = (
    "У тебе ще немає жодної звички.\n\n"
    "Натисни кнопку нижче або надішли /new, щоб додати першу."
)

MANAGE_EMPTY_TEXT = "Керувати поки нічим — у тебе немає жодної звички."

MANAGE_TEXT = (
    "<b>Керування звичками</b>\n\n"
    "Обери звичку, щоб перейменувати, змінити опис або видалити."
)


async def habits_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Повернути текст і клавіатуру для списку звичок."""
    habits = await api.habits_with_stats(telegram_id)

    if not habits:
        return EMPTY_TEXT, habits_keyboard([])

    done = sum(1 for habit in habits if habit.get("stats", {}).get("done_today"))

    # Назви звичок навмисно НЕ потрапляють у текст повідомлення: вони вже
    # є на кнопках. Заразом це знімає питання екранування — назва на
    # кшталт "Читати <щось>" зламала б розмітку HTML, а в підписі кнопки
    # вона нешкідлива, бо там розмітка не обробляється взагалі.
    text = (
        f"<b>Твої звички</b>\n"
        f"Сьогодні відмічено: {done} з {len(habits)}\n\n"
        f"Тицьни на звичку, щоб відмітити. Повторний дотик скасує відмітку."
    )

    return text, habits_keyboard(habits)


async def manage_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Список звичок у режимі керування."""
    habits = await api.list_habits(telegram_id)

    if not habits:
        return MANAGE_EMPTY_TEXT, manage_keyboard([])

    return MANAGE_TEXT, manage_keyboard(habits)


def _card_text(habit: dict) -> str:
    """Текст картки однієї звички: назва, опис і показники.

    Тут, на відміну від списку, назва й опис ТАКИ потрапляють у текст
    повідомлення — інакше картка не мала б сенсу. А отже, їх треба
    екранувати: текст іде з parse_mode=HTML, і назва на кшталт
    "Читати <щось>" зламала б розмітку, а Telegram відхилив би все
    повідомлення цілком. Та сама пастка, що колись ламала /start
    людям із символом «<» в імені.
    """
    stats = habit.get("stats") or {}
    lines = [f"<b>{html_decoration.quote(habit['name'])}</b>"]

    description = (habit.get("description") or "").strip()
    if description:
        lines.append(html_decoration.quote(description))
    else:
        lines.append("<i>опису немає</i>")

    total = stats.get("total") or 0
    current = stats.get("current_streak") or 0
    longest = stats.get("longest_streak") or 0

    lines.append("")
    lines.append(
        "Сьогодні: ✅ відмічено"
        if stats.get("done_today")
        else "Сьогодні: ⬜ ще ні"
    )
    lines.append(f"🔥 Серія: {current} {plural(current, 'день', 'дні', 'днів')}")
    lines.append(f"🏆 Найдовша: {longest} {plural(longest, 'день', 'дні', 'днів')}")
    lines.append(f"📊 Усього: {total} {plural(total, 'день', 'дні', 'днів')}")

    return "\n".join(lines)


async def habit_card_view(
    api: HabitsAPI, telegram_id: int, habit_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Картка однієї звички, або None, якщо такої звички вже немає.

    Дані беремо з habits_with_stats — тобто з того самого списку, що й
    решта екранів, а потрібну звичку знаходимо вже в памʼяті. Це один
    зайвий рядок коду замість окремого запиту, зате картка автоматично
    отримує ту саму перевірку власника: чужа чи видалена звичка просто
    не трапиться у списку, і функція поверне None.
    """
    habits = await api.habits_with_stats(telegram_id)
    habit = next((h for h in habits if h["id"] == habit_id), None)

    if habit is None:
        return None

    return _card_text(habit), habit_card_keyboard(habit_id)


async def delete_confirm_view(
    api: HabitsAPI, telegram_id: int, habit_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Екран підтвердження видалення. None — звички вже немає."""
    habits = await api.habits_with_stats(telegram_id)
    habit = next((h for h in habits if h["id"] == habit_id), None)

    if habit is None:
        return None

    total = (habit.get("stats") or {}).get("total") or 0

    # Кількість відміток називаємо прямо: саме вона робить видалення
    # незворотним, і саме про неї людина найчастіше не думає, коли тисне
    # кошик. «Видалити звичку» звучить дешево, «разом із 38 відмітками» —
    # уже ні.
    text = (
        f"Видалити <b>{html_decoration.quote(habit['name'])}</b>?\n\n"
        f"Разом зі звичкою зникнуть усі її відмітки — "
        f"{total} {plural(total, 'день', 'дні', 'днів')}.\n"
        f"Це незворотно."
    )

    return text, delete_confirm_keyboard(habit_id)
