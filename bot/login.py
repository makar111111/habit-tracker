"""Підтвердження входу у вебверсію.

Людина натискає «Увійти» у браузері, той показує посилання
t.me/бот?start=КОД. Відкривши його, вона потрапляє сюди — і бот, який
достеменно знає, хто до нього звернувся, може підтвердити цей код.

Чому потрібне ЯВНЕ підтвердження кнопкою, а не мовчазне при /start
--------------------------------------------------------------------
Спокусливо було б підтверджувати одразу, тільки-но прийшов код. Але
уяви: зловмисник відкриває трекер у себе, отримує код і надсилає тобі
своє посилання — «глянь, який бот». Ти тиснеш, бот мовчки підтверджує,
і в ЙОГО браузері відкривається сесія з ТВОЇМИ звичками. Ти навіть не
зрозумієш, що сталося.

Кнопка з прямим текстом «хтось просить доступ до твоїх звичок» цю
атаку не робить неможливою, але робить її помітною: людина бачить, що
підтверджує вхід, якого не починала, і просто не тисне.
"""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.api import ApiError, HabitsAPI
from bot.keyboards import MenuCallback

router = Router(name="login")

ASK = (
    "🔐 <b>Підтвердження входу</b>\n\n"
    "Хтось відкрив вебверсію трекера й просить доступ до твоїх звичок.\n\n"
    "Якщо це ти — підтверди. Якщо ні — просто не тисни нічого "
    "й повідом про це."
)

DONE = "Готово ✅ Повернись у браузер — сторінка вже оновилась."

FAILED = (
    "Код не спрацював: він або застарів, або вже використаний.\n"
    "Натисни «Увійти» в браузері ще раз."
)


def confirm_keyboard(token: str) -> "InlineKeyboardBuilder":
    """Кнопка підтвердження з кодом усередині.

    Код їде в callback_data, бо між натисканням /start і натисканням
    кнопки бот не має де його зберігати — стан FSM тут був би зайвим
    ускладненням заради одного рядка.

    Telegram дає на callback_data лише 64 БАЙТИ, а код — це 43 символи
    від token_urlsafe(32). Разом із префіксом «login:» виходить 49 —
    впритул, але вміщується. Саме тому префікс такий короткий.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, це я", callback_data=f"login:{token}")
    builder.button(text="Скасувати", callback_data=MenuCallback(action="habits").pack())
    builder.adjust(1)
    return builder.as_markup()


# CommandStart(deep_link=True) означає «лише /start З аргументом».
# Звичайний /start без коду сюди не потрапить — його й далі обробляє
# menu.py із привітанням. Роутер login підключений раніше за menu,
# тож із двох обробників спрацює саме потрібний.
@router.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject) -> None:
    token = (command.args or "").strip()
    if not token:
        return

    await message.answer(ASK, reply_markup=confirm_keyboard(token))


@router.callback_query(F.data.startswith("login:"))
async def confirm(callback: CallbackQuery, api: HabitsAPI) -> None:
    await callback.answer()

    if callback.from_user is None or callback.message is None:
        return

    token = callback.data.removeprefix("login:")

    try:
        await api.confirm_login(callback.from_user.id, token)
    except ApiError:
        # Найчастіше — код протух (5 хвилин) або вже використаний.
        # Це не поломка, тож і текст спокійний, із підказкою що робити.
        await callback.bot.send_message(callback.message.chat.id, FAILED)
        return

    await callback.bot.send_message(callback.message.chat.id, DONE)
