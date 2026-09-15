"""Нагадування за особистою годиною, часовим поясом і розкладом звичок.

API зберігає день успішної доставки, тож звичайний перезапуск не повторює
нагадування. Telegram і API не мають спільної транзакції: якщо повідомлення
доставлено, а підтвердження API загубилося, наступна спроба може повторити
його. Цей випадок лишаємо в журналі, а не оголошуємо гарантію exactly-once.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.utils.text_decorations import html_decoration

from bot.api import ApiError, HabitsAPI
from bot.keyboards import reminder_keyboard
from bot.plural import plural
from bot.schedule import is_planned_on
from config import REMINDER_HOUR


def next_run_at(now: datetime, hour: int = REMINDER_HOUR, minute: int = 0) -> datetime:
    """Найближчий момент у майбутньому, коли годинник покаже hour:minute.

    Чиста функція: жодного datetime.now() усередині. Завдяки цьому
    тестується без реального очікування — підставляєш «зараз» і звіряєш
    відповідь, замість того щоб чекати справжню годину чи підміняти
    системний годинник.
    """
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        # Час уже минув сьогодні (або це рівно він) — переносимо на завтра.
        # candidate <= now, а не <, навмисно: якщо годинник рівно 20:00:00,
        # маємо чекати ЗАВТРАШНІХ 20:00, а не спрацювати ще раз негайно.
        candidate += timedelta(days=1)
    return candidate


def reminder_day(target: dict, now: datetime) -> date | None:
    """День нагадування у часовому поясі людини; None — ще не час або вже було."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Годинник нагадувань має містити часовий пояс")
    local = now.astimezone(ZoneInfo(target["timezone"]))
    day = local.date()
    if local.hour < target["reminder_hour"]:
        return None
    # Зміна пояса на захід може повернути календар на попередній день.
    # Уже пройдені дні теж пропускаємо: API зберігає останній день монотонно.
    last_day = target.get("last_reminder_day")
    if last_day and last_day >= day.isoformat():
        return None
    return day


def pluralize_habits(count: int) -> str:
    """Українська форма слова «звичка» залежно від числа.

    1 звичку, 2 звички, 5 звичок — але й 21 звичку, 22 звички: саме
    правило (і виняток на 11-14) живе в bot/plural.py, бо потрібне
    ще й картці звички для слова «день».
    """
    return plural(count, "звичку", "звички", "звичок")


REMINDER_ALL_DONE = "Нагадування 🔔\n\nУсе відмічено 🎉"


def reminder_text(names: list[str]) -> str:
    """Текст нагадування зі списком невідміченого.

    Порожній список означає «нагадувати нема про що»: send_reminders
    відсіює його ще до надсилання, а після відміток із кнопок нагадування
    перемальовується на REMINDER_ALL_DONE.
    """
    if not names:
        return REMINDER_ALL_DONE
    listed = "\n".join(f"• {html_decoration.quote(name)}" for name in names)
    return (
        f"Нагадування 🔔\n\n"
        f"Ще не відмічено {len(names)} {pluralize_habits(len(names))}:\n"
        f"{listed}"
    )


async def undone_habits(
    api: HabitsAPI, telegram_id: int, day: date | None = None
) -> list[dict]:
    """Невідмічені звички, заплановані на місцевий день людини."""
    habits = await api.habits_with_stats(telegram_id)
    return [
        habit
        for habit in habits
        if (is_planned_on(habit, day) if day else habit.get("due_today", True))
        and not (habit.get("stats") or {}).get("done_today")
    ]


async def undone_habit_names(
    api: HabitsAPI, telegram_id: int, day: date | None = None
) -> list[str]:
    """Лише назви невідміченого — для тексту нагадування."""
    return [habit["name"] for habit in await undone_habits(api, telegram_id, day)]


async def send_reminders(
    bot: Bot, api: HabitsAPI, *, now: datetime | None = None
) -> None:
    """Одна перевірка особистих нагадувань. now — aware час для відтворюваності.

    Кожен крок обгорнутий окремо, щоб одна людина не зіпсувала розсилку
    всім іншим: якщо API не відповів для когось одного — пропускаємо
    його й ідемо далі; якщо людина заблокувала бота чи видалила
    акаунт — те саме. Розсилка на 500 людей не має спинятись через
    одну проблемну.
    """
    now = now or datetime.now(timezone.utc)
    try:
        targets = await api.list_reminder_targets()
    except ApiError:
        logging.exception("Не вдалося отримати список користувачів для нагадування")
        return

    for target in targets:
        telegram_id = target["telegram_id"]
        try:
            day = reminder_day(target, now)
        except KeyError, ValueError:
            logging.warning(
                "Некоректні налаштування нагадування користувача %s", telegram_id
            )
            continue
        if day is None:
            continue

        try:
            habits = await undone_habits(api, telegram_id, day)
        except ApiError:
            logging.warning("Не вдалося перевірити звички користувача %s", telegram_id)
            continue

        if not habits:
            # Усе вже відмічено — нагадувати нема про що, і мовчання
            # тут не поломка, а правильна поведінка.
            continue

        try:
            # Кнопки прямо під нагадуванням: відмітити можна одним дотиком,
            # не набираючи /habits. Назви лишаються і в тексті — у
            # пуш-сповіщенні на заблокованому екрані кнопок не видно.
            await bot.send_message(
                telegram_id,
                reminder_text([habit["name"] for habit in habits]),
                reply_markup=reminder_keyboard(habits, day),
            )
        except Exception:
            # Найчастіша причина — людина заблокувала бота чи видалила
            # акаунт. Це не наша помилка, і вона не має рвати розсилку
            # решті. Ловимо широко (Exception, не TelegramAPIError):
            # мало який саме виняток кине aiogram у такому випадку,
            # а точність тут не варта ризику зупинити всю розсилку.
            logging.warning(
                "Не вдалося надіслати нагадування користувачу %s", telegram_id
            )
            continue

        try:
            await api.mark_reminder_sent(telegram_id, day)
        except ApiError:
            logging.warning(
                "Нагадування користувачу %s доставлено, але день не збережено; "
                "можливе повторне надсилання",
                telegram_id,
            )


async def reminder_loop(bot: Bot, api: HabitsAPI) -> None:
    """Перевіряти нагадування одразу після запуску та щохвилини.

    Запускається окремою задачею (asyncio.create_task) паралельно
    з polling. Зупиняється через asyncio.CancelledError, коли її
    скасовують при завершенні бота (bot/__main__.py) — це штатний
    спосіб зупинити нескінченний цикл, а не помилка.
    """
    logging.info("Перевірка особистих нагадувань кожні 60 с")
    while True:
        try:
            await send_reminders(bot, api)
        except Exception:
            # Помилка не вимикає фонову задачу: наступна спроба за хвилину.
            logging.exception("Помилка при розсилці нагадувань")
        await asyncio.sleep(60)
