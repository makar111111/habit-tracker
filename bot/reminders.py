"""Щоденне нагадування о встановленій годині.

Кому пишемо: усім telegram-користувачам бота (не локальному браузерному —
у нього немає telegram_id, надсилати нікуди), в яких лишилась хоч одна
невідмічена сьогодні звичка.

Механізм — власний нескінченний цикл на asyncio, а не готова бібліотека
на кшталт APScheduler: рахуємо час до найближчого 20:00, засинаємо,
прокидаємось, шлемо нагадування, рахуємо знову. Простіше й наочніше —
і саме на такому циклі видно, як живе довготривалий асинхронний фон,
а не як виглядає виклик готового інструмента.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from bot.api import ApiError, HabitsAPI
from bot.plural import plural
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


def pluralize_habits(count: int) -> str:
    """Українська форма слова «звичка» залежно від числа.

    1 звичку, 2 звички, 5 звичок — але й 21 звичку, 22 звички: саме
    правило (і виняток на 11-14) живе в bot/plural.py, бо потрібне
    ще й картці звички для слова «день».
    """
    return plural(count, "звичку", "звички", "звичок")


def reminder_text(names: list[str]) -> str:
    """Текст нагадування зі списком невідміченого.

    Порожній список сюди подавати не варто — це означає «нагадувати
    нема про що», і викликач (send_reminders) відсіює такий випадок
    заздалегідь, до мережевого виклику.
    """
    listed = "\n".join(f"• {name}" for name in names)
    return (
        f"Нагадування 🔔\n\n"
        f"Ще не відмічено {len(names)} {pluralize_habits(len(names))}:\n"
        f"{listed}"
    )


async def undone_habit_names(api: HabitsAPI, telegram_id: int) -> list[str]:
    """Назви звичок, які людина сьогодні ще не відмітила."""
    habits = await api.habits_with_stats(telegram_id)
    return [h["name"] for h in habits if not h["stats"].get("done_today")]


async def send_reminders(bot: Bot, api: HabitsAPI) -> None:
    """Одна розсилка: пройтись по всіх telegram-користувачах, написати тим,
    у кого лишилось невідмічене.

    Кожен крок обгорнутий окремо, щоб одна людина не зіпсувала розсилку
    всім іншим: якщо API не відповів для когось одного — пропускаємо
    його й ідемо далі; якщо людина заблокувала бота чи видалила
    акаунт — те саме. Розсилка на 500 людей не має спинятись через
    одну проблемну.
    """
    try:
        telegram_ids = await api.list_telegram_users()
    except ApiError:
        logging.exception("Не вдалося отримати список користувачів для нагадування")
        return

    for telegram_id in telegram_ids:
        try:
            names = await undone_habit_names(api, telegram_id)
        except ApiError:
            logging.warning(
                "Не вдалося перевірити звички користувача %s", telegram_id
            )
            continue

        if not names:
            # Усе вже відмічено — нагадувати нема про що, і мовчання
            # тут не поломка, а правильна поведінка.
            continue

        try:
            await bot.send_message(telegram_id, reminder_text(names))
        except Exception:
            # Найчастіша причина — людина заблокувала бота чи видалила
            # акаунт. Це не наша помилка, і вона не має рвати розсилку
            # решті. Ловимо широко (Exception, не TelegramAPIError):
            # мало який саме виняток кине aiogram у такому випадку,
            # а точність тут не варта ризику зупинити всю розсилку.
            logging.warning(
                "Не вдалося надіслати нагадування користувачу %s", telegram_id
            )


async def reminder_loop(bot: Bot, api: HabitsAPI) -> None:
    """Нескінченний цикл: чекати до найближчого REMINDER_HOUR, надіслати,
    повторити.

    Запускається окремою задачею (asyncio.create_task) паралельно
    з polling. Зупиняється через asyncio.CancelledError, коли її
    скасовують при завершенні бота (bot/__main__.py) — це штатний
    спосіб зупинити нескінченний цикл, а не помилка.
    """
    # Час друкуємо в лог разом із назвою поясового зсуву навмисно.
    # Бот і API рахують "сьогодні" кожен за своїм годинником, і поки
    # вони на одній машині, це той самий годинник. У контейнерах —
    # уже ні: за замовчуванням усередині UTC, тож "20:00" перетворилося б
    # на 23:00 за Києвом, а відмітка, поставлена ввечері, могла б лягти
    # на "завтра". docker-compose.yml задає обом контейнерам однаковий TZ,
    # і саме цей рядок дає це швидко перевірити, не гадаючи.
    now = datetime.now()
    logging.info(
        "Годинник бота: %s (%s). Нагадування о %d:00",
        now.strftime("%Y-%m-%d %H:%M"),
        now.astimezone().tzname() or "локальний час",
        REMINDER_HOUR,
    )

    while True:
        wait_seconds = (next_run_at(datetime.now()) - datetime.now()).total_seconds()
        logging.info("Наступне нагадування через %.0f с", wait_seconds)
        await asyncio.sleep(wait_seconds)

        try:
            await send_reminders(bot, api)
        except Exception:
            # Непередбачена помилка в самій розсилці (а не в конкретному
            # користувачі — ті вже оброблені всередині send_reminders)
            # не повинна зупиняти цикл назавжди. Наступного вечора
            # спробуємо ще раз замість того, щоб бот тихо перестав
            # нагадувати комусь узагалі.
            logging.exception("Помилка при розсилці нагадувань")
