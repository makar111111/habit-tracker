"""Налаштування нагадувань із бота: /settings.

Раніше змінити годину чи часовий пояс, або навіть вимкнути нагадування,
можна було лише у вебверсії. Людина, яка користується тільки Telegram,
мала єдиний спосіб зупинити нагадування — заблокувати бота.

Екрани:

    /settings ── головний екран ──🔕/🔔──> перемкнути нагадування
                    │        │
              🕗 година   🌍 пояс ──✏️──> введення назви поясу текстом
                    ▼        ▼
               сітка 0–23  готові пояси

API саме перевіряє значення (година 0–23, відомий пояс), але пояс, введений
вручну, перевіряємо ще й тут — див. resolve_timezone.
"""

from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.text_decorations import html_decoration

from bot.api import HabitsAPI
from bot.keyboards import MenuCallback

router = Router(name="settings")

# Ті самі готові варіанти, що й у вебверсії (SettingsScreen.tsx), плюс
# Варшава — поширений пояс для українців за кордоном.
PRESET_TIMEZONES = (
    "Europe/Kyiv",
    "Europe/Warsaw",
    "Europe/London",
    "UTC",
    "America/New_York",
)

STALE = "Ця кнопка вже неактуальна. Відкрий /settings ще раз."

ASK_TIMEZONE = (
    "Надішли назву часового поясу, наприклад <code>Europe/Berlin</code> "
    "або просто <code>Berlin</code>.\n\n"
    "Надішли /cancel, щоб лишити як було."
)

UNKNOWN_TIMEZONE = (
    "Не знаю такого часового поясу: {value}.\n"
    "Спробуй у форматі <code>Europe/Berlin</code> або надішли /cancel."
)


class SettingsCallback(CallbackData, prefix="set"):
    """Кнопки налаштувань.

    action: "home", "reminders" (value "on"/"off"), "hours", "hour" (value —
    година), "zones", "zone" (value — назва поясу), "zone_manual".
    Дія й значення явні, а не «перемикач»: підпис кнопки показує стан на
    момент відкриття екрана, а в браузері його могли вже змінити.
    Найдовше — "set:zone:America/New_York" — 26 байтів із 64.
    """

    action: str
    value: str = ""


class SettingsDialog(StatesGroup):
    timezone = State()


@lru_cache(maxsize=1)
def _zones_by_lower() -> dict[str, str]:
    """Усі відомі пояси: назва в нижньому регістрі → канонічна назва.

    available_timezones() сканує базу поясів — сотні файлів, тож кешуємо:
    набір поясів не змінюється, поки процес бота живе.
    """
    return {zone.lower(): zone for zone in available_timezones()}


def resolve_timezone(text: str) -> str | None:
    """Канонічна назва поясу з того, що ввела людина, або None.

    ZoneInfo чутлива до регістру, але на Windows файлова система — ні:
    "europe/kyiv" там «спрацювала» б і лягла в базу в неканонічному
    вигляді. Тому шукаємо збіг без урахування регістру й повертаємо
    канонічне написання. Прийнятне й саме місто ("warsaw"), якщо воно
    однозначне; пробіли стають "_", як у назвах поясів ("New York").
    """
    value = text.strip().replace(" ", "_").lower()
    if not value:
        return None

    zones = _zones_by_lower()
    if value in zones:
        return zones[value]

    by_city = [zone for low, zone in zones.items() if low.rsplit("/", 1)[-1] == value]
    return by_city[0] if len(by_city) == 1 else None


def settings_text(user: dict, now: datetime | None = None) -> str:
    """Головний екран: стан нагадувань і пояс із поточним часом у ньому.

    Поточний час — щоб людина одразу побачила, чи пояс правильний:
    «зараз 21:05» перевірити легше, ніж згадати, чи Kyiv — це UTC+2 чи +3.
    """
    zone = user["timezone"]
    local = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(zone))
    hour = f"{user['reminder_hour']:02d}:00"
    reminders = f"увімкнені, о {hour}" if user["reminders_enabled"] else "вимкнені"
    return (
        "<b>⚙️ Налаштування</b>\n\n"
        f"🔔 Нагадування: {reminders}\n"
        f"🌍 Часовий пояс: {html_decoration.quote(zone)} (зараз {local:%H:%M})\n\n"
        "Нагадування приходить щодня у вибрану годину за твоїм поясом, "
        "якщо є невідмічені звички."
    )


def settings_keyboard(user: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if user["reminders_enabled"]:
        builder.button(
            text="🔕 Вимкнути нагадування",
            callback_data=SettingsCallback(action="reminders", value="off"),
        )
    else:
        builder.button(
            text="🔔 Увімкнути нагадування",
            callback_data=SettingsCallback(action="reminders", value="on"),
        )
    builder.button(
        text="🕗 Змінити годину", callback_data=SettingsCallback(action="hours")
    )
    builder.button(
        text="🌍 Змінити пояс", callback_data=SettingsCallback(action="zones")
    )
    builder.button(text="⬅️ До списку", callback_data=MenuCallback(action="habits"))
    builder.adjust(1)
    return builder.as_markup()


def hours_keyboard(current: int) -> InlineKeyboardMarkup:
    """Сітка 00–23 по шість у рядку; поточна година позначена."""
    builder = InlineKeyboardBuilder()
    for hour in range(24):
        mark = "✅" if hour == current else ""
        builder.button(
            text=f"{mark}{hour:02d}",
            callback_data=SettingsCallback(action="hour", value=str(hour)),
        )
    builder.button(text="⬅️ Назад", callback_data=SettingsCallback(action="home"))
    builder.adjust(6, 6, 6, 6, 1)
    return builder.as_markup()


def zones_keyboard(current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for zone in PRESET_TIMEZONES:
        mark = "✅ " if zone == current else ""
        builder.button(
            text=f"{mark}{zone}",
            callback_data=SettingsCallback(action="zone", value=zone),
        )
    builder.button(
        text="✏️ Ввести вручну", callback_data=SettingsCallback(action="zone_manual")
    )
    builder.button(text="⬅️ Назад", callback_data=SettingsCallback(action="home"))
    builder.adjust(1)
    return builder.as_markup()


async def _edit(
    callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup
) -> None:
    """Перемалювати екран під кнопкою (або надіслати новий, якщо старий недоступний)."""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest as error:
            # Дотик по вже вибраній годині чи поясу нічого не змінює —
            # Telegram відмовляється зберігати незмінене повідомлення.
            if "message is not modified" not in str(error):
                raise
    elif message is not None:
        await callback.bot.send_message(message.chat.id, text, reply_markup=keyboard)


# ---------- вхід ----------


@router.message(Command("settings"))
async def handle_settings(message: Message, api: HabitsAPI) -> None:
    if message.from_user is None:
        return
    user = await api.get_me(message.from_user.id)
    await message.answer(settings_text(user), reply_markup=settings_keyboard(user))


# ---------- кнопки ----------


@router.callback_query(SettingsCallback.filter())
async def handle_settings_button(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    api: HabitsAPI,
    state: FSMContext,
) -> None:
    """Усі кнопки налаштувань в одному обробнику — дії короткі й однотипні.

    Кожне значення з кнопки звіряємо: callback_data шле клієнт, і
    модифікований клієнт може вписати годину 99 чи вигаданий пояс.
    """
    if callback.from_user is None:
        return
    telegram_id = callback.from_user.id
    action, value = callback_data.action, callback_data.value

    if action == "zone_manual":
        # Новий стан мовчки затер би почате створення чи редагування звички —
        # і недописана звичка просто зникла б. Інші кнопки налаштувань стан
        # не чіпають, тож їм посеред діалогу дозволено.
        if await state.get_state() is not None:
            await callback.answer(
                "Спершу завершимо почате.\nНадішли /cancel, якщо передумав.",
                show_alert=True,
            )
            return
        await state.set_state(SettingsDialog.timezone)
        await callback.answer()
        if callback.message is not None:
            await callback.bot.send_message(callback.message.chat.id, ASK_TIMEZONE)
        return

    note = ""
    if action == "reminders" and value in ("on", "off"):
        user = await api.update_settings(telegram_id, reminders_enabled=value == "on")
        note = (
            "Нагадування увімкнено 🔔" if value == "on" else "Нагадування вимкнено 🔕"
        )
    elif action == "hour" and value.isdigit() and 0 <= int(value) <= 23:
        user = await api.update_settings(telegram_id, reminder_hour=int(value))
        note = f"Нагадування о {int(value):02d}:00"
    elif action == "zone" and value in PRESET_TIMEZONES:
        user = await api.update_settings(telegram_id, timezone=value)
        note = f"Пояс: {value}"
    elif action in ("home", "hours", "zones"):
        user = await api.get_me(telegram_id)
    else:
        await callback.answer(STALE, show_alert=True)
        return

    if action == "hours":
        text, keyboard = (
            "🕗 О котрій годині нагадувати?",
            hours_keyboard(user["reminder_hour"]),
        )
    elif action == "zones":
        text = (
            "🌍 Обери часовий пояс.\n\n"
            f"Зараз: {html_decoration.quote(user['timezone'])}"
        )
        keyboard = zones_keyboard(user["timezone"])
    else:
        # Після будь-якої зміни — назад на головний екран: там видно результат.
        text, keyboard = settings_text(user), settings_keyboard(user)

    # Відповідаємо ПІСЛЯ запитів до API — як у toggle_checkin: якщо запит
    # впаде, on_error відповість на натискання сам, одним викликом.
    await callback.answer(note)
    await _edit(callback, text, keyboard)


# ---------- введення поясу текстом ----------


@router.message(Command("cancel"), StateFilter(SettingsDialog))
async def cancel_timezone(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано. Пояс не змінено.")


@router.message(StateFilter(SettingsDialog), F.text.startswith("/"))
async def command_during_timezone(message: Message) -> None:
    """Та сама пастка, що в new_habit.py: команда не має стати назвою поясу."""
    await message.answer("Спершу надішли назву поясу.\nАбо /cancel, якщо передумав.")


@router.message(SettingsDialog.timezone, F.text)
async def got_timezone(message: Message, state: FSMContext, api: HabitsAPI) -> None:
    if message.from_user is None:
        return

    zone = resolve_timezone(message.text or "")
    if zone is None:
        # Лишаємося в стані: людина може просто надіслати іншу назву.
        await message.answer(
            UNKNOWN_TIMEZONE.format(value=html_decoration.quote(message.text or ""))
        )
        return

    user = await api.update_settings(message.from_user.id, timezone=zone)
    # Стан чистимо ПІСЛЯ успішного запиту — якщо API впаде, людина лишиться
    # в діалозі й зможе надіслати назву ще раз (як у manage._apply).
    await state.clear()
    await message.answer(
        f"Збережено ✅\n\n{settings_text(user)}", reply_markup=settings_keyboard(user)
    )


@router.message(SettingsDialog.timezone)
async def timezone_must_be_text(message: Message) -> None:
    await message.answer("Потрібен текст: назва поясу, наприклад Europe/Berlin.")
