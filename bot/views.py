"""Складання екрана зі списком звичок.

Винесено окремо, бо цей самий екран малюють двоє: команда /habits
(створює нове повідомлення) і натискання кнопки (перемальовує старе).
Якби код лежав в одному з них, другий мусив би його імпортувати —
і вийшов би заплутаний звʼязок між обробниками.
"""

from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.text_decorations import html_decoration

from bot.api import HabitsAPI
from bot.keyboards import (
    archive_keyboard,
    delete_confirm_keyboard,
    habit_card_keyboard,
    habits_keyboard,
    manage_keyboard,
    templates_keyboard,
)
from bot.plural import plural
from bot.schedule import EVERY_DAY, is_planned_on, schedule_label
from bot.templates import TEMPLATES

EMPTY_TEXT = (
    "У тебе ще немає жодної звички.\n\n"
    "Почни з готової — один дотик, і вона в списку. "
    "Або створи свою кнопкою «➕ Своя звичка»."
)

TEMPLATES_TEXT = (
    "<b>Готові звички</b> 📋\n\nОдин дотик — і звичка в списку. Можна обрати кілька."
)

TEMPLATES_ALL_TAKEN_TEXT = (
    "<b>Готові звички</b> 📋\n\n"
    "Усі шаблони вже додано. Створи свою або повертайся до списку."
)

MANAGE_EMPTY_TEXT = "Керувати поки нічим — у тебе немає жодної звички."

ARCHIVE_ONLY_TEXT = (
    "<b>Керування звичками</b>\n\n"
    "Активних звичок немає — усі в архіві. Відкрий архів, щоб відновити."
)

MANAGE_TEXT = (
    "<b>Керування звичками</b>\n\n"
    "Обери звичку, щоб перейменувати, змінити опис, перенести в архів "
    "або видалити."
)

ARCHIVE_TEXT = (
    "<b>Архів</b> 📦\n\n"
    "Звички на паузі: не показуються у списку й не нагадують, "
    "але вся історія відміток збережена. Обери, щоб відновити або видалити."
)

ARCHIVE_EMPTY_TEXT = "Архів порожній."


async def habits_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Повернути текст і клавіатуру для списку звичок."""
    habits = await api.habits_with_stats(telegram_id)

    if not habits:
        # Порожній список — одразу готові звички, а не лише «натисни ➕».
        return await templates_view(api, telegram_id)

    planned = [habit for habit in habits if habit.get("due_today", True)]
    done = sum(1 for habit in planned if habit.get("stats", {}).get("done_today"))

    # Назви звичок навмисно НЕ потрапляють у текст повідомлення: вони вже
    # є на кнопках. Заразом це знімає питання екранування — назва на
    # кшталт "Читати <щось>" зламала б розмітку HTML, а в підписі кнопки
    # вона нешкідлива, бо там розмітка не обробляється взагалі.
    text = (
        f"<b>Твої звички</b>\n"
        f"Сьогодні відмічено: {done} з {len(planned)}\n\n"
        f"Тицьни на звичку, щоб відмітити. Повторний дотик скасує відмітку."
    )
    if len(planned) < len(habits):
        text += "\n💤 — сьогодні не заплановано."

    return text, habits_keyboard(habits)


async def templates_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Екран готових звичок — без тих, що вже є у людини.

    Уже додані ховаємо за назвою, враховуючи й архів: інакше шаблон
    мовчки створив би дублікат звички, яка стоїть на паузі, — а правильний
    шлях для неї «♻️ Відновити», зі збереженою історією.
    """
    everything = await api.list_habits(telegram_id, include_archived=True)
    taken = {habit["name"].casefold() for habit in everything}
    active = [habit for habit in everything if not habit.get("archived_at")]
    available = [t for t in TEMPLATES if t.name.casefold() not in taken]

    if not active:
        text = EMPTY_TEXT
    elif available:
        text = TEMPLATES_TEXT
    else:
        text = TEMPLATES_ALL_TAKEN_TEXT
    return text, templates_keyboard(available, has_habits=bool(active))


async def manage_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Список звичок у режимі керування (+ вхід в архів, якщо він не порожній)."""
    everything = await api.list_habits(telegram_id, include_archived=True)
    active = [habit for habit in everything if not habit.get("archived_at")]
    archived_count = len(everything) - len(active)

    if not active:
        # Усі звички в архіві — «керувати нічим» було б неправдою:
        # архів є, і до нього треба дати дорогу.
        text = MANAGE_EMPTY_TEXT if not archived_count else ARCHIVE_ONLY_TEXT
        return text, manage_keyboard([], archived_count)

    return MANAGE_TEXT, manage_keyboard(active, archived_count)


async def archive_view(
    api: HabitsAPI, telegram_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Список архівних звичок."""
    everything = await api.list_habits(telegram_id, include_archived=True)
    archived = [habit for habit in everything if habit.get("archived_at")]

    if not archived:
        return ARCHIVE_EMPTY_TEXT, archive_keyboard([])

    return ARCHIVE_TEXT, archive_keyboard(archived)


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
    lines.append(f"📅 Розклад: {schedule_label(habit.get('weekdays') or EVERY_DAY)}")
    archived_at = habit.get("archived_at")
    if archived_at:
        # «Сьогодні: ще ні» для звички на паузі звучало б як докір.
        day = date.fromisoformat(archived_at)
        lines.append(f"📦 В архіві з {day:%d.%m.%Y}")
    elif stats.get("done_today"):
        lines.append("Сьогодні: ✅ відмічено")
    elif not habit.get("due_today", True):
        lines.append("Сьогодні: 💤 не заплановано")
    else:
        lines.append("Сьогодні: ⬜ ще ні")
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
    # include_archived: картка відкривається і з архіву, а кнопка «Скасувати»
    # на підтвердженні видалення теж веде сюди — для будь-якої звички.
    habits = await api.habits_with_stats(telegram_id, include_archived=True)
    habit = next((h for h in habits if h["id"] == habit_id), None)

    if habit is None:
        return None

    archived = bool(habit.get("archived_at"))
    yesterday = None if archived else await _yesterday_state(api, telegram_id, habit)
    keyboard = habit_card_keyboard(habit_id, archived=archived, yesterday=yesterday)
    return _card_text(habit), keyboard


async def _yesterday_state(
    api: HabitsAPI, telegram_id: int, habit: dict
) -> tuple[date, bool] | None:
    """(вчорашній день, чи відмічений) — або None, якщо вчора не було в розкладі.

    «Вчора» — у часовому поясі людини (api.today), а не за годинником
    бота: сервер бота може жити в іншому поясі, і тоді «вчора» зсунулося б.
    """
    day = await api.today(telegram_id) - timedelta(days=1)
    if not is_planned_on(habit, day):
        return None
    return day, await api.is_checked_in(telegram_id, habit["id"], day)


async def delete_confirm_view(
    api: HabitsAPI, telegram_id: int, habit_id: int
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Екран підтвердження видалення. None — звички вже немає."""
    habits = await api.habits_with_stats(telegram_id, include_archived=True)
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
