"""Команди: /start, /help, /habits, /export.

Router — це набір обробників, який потім підключається до диспетчера.
Розкладати бота по роутерах не обовʼязково, але коли їх стане десяток,
різниця між «зрозуміло» і «файл на тисячу рядків» буде саме тут.
"""

import json

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message
from aiogram.utils.text_decorations import html_decoration

from bot.api import HabitsAPI
from bot.plural import plural
from bot.views import habits_view

router = Router(name="menu")

HELP_TEXT = (
    "<b>Трекер звичок</b>\n\n"
    "/habits — список звичок і відмітки\n"
    "/new — додати звичку\n"
    "/manage — перейменувати, змінити опис, архів, видалити\n"
    "/settings — година нагадувань, часовий пояс, увімкнути чи вимкнути\n"
    "/cancel — перервати початий діалог\n"
    "/support — підтримати проєкт донатом у Stars\n"
    "/export — забрати всі свої дані файлом\n"
    "/help — ця довідка\n\n"
    "Бот нагадує про заплановані невідмічені звички у вибрану годину "
    "за твоїм часовим поясом. Змінити її — /settings.\n\n"
    "Той самий список відкривається у браузері — "
    "це один застосунок із двома входами."
)


@router.message(CommandStart())
async def handle_start(message: Message, api: HabitsAPI) -> None:
    """Перше знайомство.

    Окремої реєстрації немає: API заводить користувача сам, побачивши
    новий telegram_id. Нам лишається передати імʼя, щоб у базі був
    не голий номер, а людина.

    Аргумент api сюди підставляє aiogram — так само, як FastAPI підставляє
    залежності через Depends. Ми поклали клієнт у диспетчер, а aiogram
    звіряє назву аргументу й передає його в кожен обробник, що просить.
    """
    if message.from_user is None:
        return

    await api.set_name(message.from_user.id, message.from_user.full_name)

    text, keyboard = await habits_view(api, message.from_user.id)

    # Ім'я з Telegram людина вписує собі сама, і в ньому легко трапляється
    # "<" — смайлик "<3", нікнейм "<Тінь>" тощо. Текст іде з parse_mode=HTML
    # на весь бот (bot/__main__.py), тому сирий "<" ламає розмітку:
    # Telegram не впізнає такий "тег" і відхиляє все повідомлення разом
    # із привітанням і списком звичок — назавжди, для кожного /start,
    # аж поки людина не перейменується. html_decoration.quote саме для
    # цього — екранує спецсимволи HTML, лишаючи текст текстом.
    # views.py вже уникає цієї пастки для назв звичок; тут та сама обережність
    # потрібна для чужого, непідконтрольного нам рядка — імені користувача.
    name = html_decoration.quote(message.from_user.first_name)
    await message.answer(
        f"Вітаю, {name}! 👋\n\n{text}",
        reply_markup=keyboard,
    )


@router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("habits"))
async def handle_habits(message: Message, api: HabitsAPI) -> None:
    """Показати список звичок новим повідомленням."""
    if message.from_user is None:
        return

    text, keyboard = await habits_view(api, message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("export"))
async def handle_export(message: Message, api: HabitsAPI) -> None:
    """Надіслати файлом усі звички й відмітки людини.

    Дані трекера — чужі, не наші: людина має могти забрати їх і піти.
    Ендпоінт /users/me/export уже віддає готовий JSON одним знімком бази,
    тож бот лише пересилає його документом.
    """
    if message.from_user is None:
        return

    data = await api.export(message.from_user.id)
    # Підпис рахуємо з того ж файла, що й відправляємо: інакше числа
    # могли б розійтися з вмістом, якби між двома запитами щось змінилось.
    export = json.loads(data)
    habits = len(export.get("habits", []))
    checkins = len(export.get("checkins", []))

    await message.answer_document(
        BufferedInputFile(data, filename="habits-export.json"),
        caption=(
            f"📦 Твої дані: {habits} {plural(habits, 'звичка', 'звички', 'звичок')}, "
            f"{checkins} {plural(checkins, 'відмітка', 'відмітки', 'відміток')}.\n\n"
            "Формат JSON — його читає і людина, і програма."
        ),
    )
