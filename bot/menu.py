"""Команди: /start, /help, /habits.

Router — це набір обробників, який потім підключається до диспетчера.
Розкладати бота по роутерах не обовʼязково, але коли їх стане десяток,
різниця між «зрозуміло» і «файл на тисячу рядків» буде саме тут.
"""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

from bot.api import HabitsAPI
from bot.views import habits_view

router = Router(name="menu")

HELP_TEXT = (
    "<b>Трекер звичок</b>\n\n"
    "/habits — список звичок і відмітки\n"
    "/new — додати звичку\n"
    "/manage — перейменувати, змінити опис, архів, видалити\n"
    "/cancel — перервати початий діалог\n"
    "/support — підтримати проєкт донатом у Stars\n"
    "/help — ця довідка\n\n"
    "Бот нагадує про заплановані невідмічені звички у вибрану годину "
    "за твоїм часовим поясом. Налаштування нагадувань — у вебверсії.\n\n"
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
