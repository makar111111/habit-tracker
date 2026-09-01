"""Складання екрана зі списком звичок.

Винесено окремо, бо цей самий екран малюють двоє: команда /habits
(створює нове повідомлення) і натискання кнопки (перемальовує старе).
Якби код лежав в одному з них, другий мусив би його імпортувати —
і вийшов би заплутаний звʼязок між обробниками.
"""

from aiogram.types import InlineKeyboardMarkup

from bot.api import HabitsAPI
from bot.keyboards import habits_keyboard

EMPTY_TEXT = (
    "У тебе ще немає жодної звички.\n\n"
    "Натисни кнопку нижче або надішли /new, щоб додати першу."
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
