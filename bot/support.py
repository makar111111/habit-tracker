"""Підтримка проєкту донатом у Telegram Stars: /support і /paysupport.

Як іде платіж — три кроки, і кожен обробляється окремо:
  1. /support → людина обирає суму → бот надсилає РАХУНОК (invoice);
  2. людина тисне «Оплатити» → Telegram питає бота «приймаєш?»
     (pre_checkout_query) — відповісти треба за 10 секунд;
  3. гроші списано → приходить повідомлення з successful_payment.

Дякуємо лише на кроці 3. На кроці 2 платіж ще може не пройти, а
натискання кнопки на кроці 1 взагалі нічого не означає.

Stars (валюта "XTR") — цифрова валюта Telegram. provider_token для неї
не потрібен: платіжного провайдера немає, гроші йдуть через сам Telegram.
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.text_decorations import html_decoration

from config import SUPPORT_CONTACT

router = Router(name="support")

STARS = "XTR"

# Суми фіксовані, а не «введи будь-яку»: менше кроків для людини і
# нема чого валідувати, крім «чи є сума в списку».
AMOUNTS = (50, 100, 250)

PAYLOAD_PREFIX = "donate:"

ASK = (
    "💛 <b>Підтримати трекер</b>\n\n"
    "Трекер безкоштовний, а сервер і бот працюють щодня. "
    "Якщо він тобі допомагає — можна задонатити в Telegram Stars.\n\n"
    "Це добровільно: жодна функція не зникне без донату."
)

THANKS = "Дякую за підтримку! 💛 Отримано {amount} ⭐"

BAD_AMOUNT = "Така сума недоступна. Відкрий /support ще раз."


def paysupport_text() -> str:
    # Значення з .env, а текст іде з parse_mode=HTML — "<" у ньому зламав би
    # усе повідомлення (та сама пастка, що з іменем у menu.py).
    contact = (
        f"Напиши {html_decoration.quote(SUPPORT_CONTACT)}"
        if SUPPORT_CONTACT
        else "Напиши розробникові через сторінку бота"
    )
    return (
        "<b>Питання щодо оплати</b>\n\n"
        "Донати добровільні й нічого не відкривають у застосунку. "
        "Якщо оплата пройшла помилково, кошти можна повернути.\n\n"
        f"{contact} і вкажи дату та суму платежу."
    )


def amounts_keyboard():
    builder = InlineKeyboardBuilder()
    for amount in AMOUNTS:
        builder.button(text=f"{amount} ⭐", callback_data=f"support:{amount}")
    builder.adjust(len(AMOUNTS))
    return builder.as_markup()


def parse_payload(payload: str) -> int | None:
    """Сума з payload рахунку, або None, якщо рахунок не наш чи сума чужа."""
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    raw = payload.removeprefix(PAYLOAD_PREFIX)
    if not raw.isdigit() or int(raw) not in AMOUNTS:
        return None
    return int(raw)


@router.message(Command("support"))
async def handle_support(message: Message) -> None:
    await message.answer(ASK, reply_markup=amounts_keyboard())


@router.message(Command("paysupport"))
async def handle_paysupport(message: Message) -> None:
    await message.answer(paysupport_text())


@router.callback_query(F.data.startswith("support:"))
async def choose_amount(callback: CallbackQuery) -> None:
    # callback_data приходить від клієнта, і модифікований клієнт може
    # надіслати що завгодно — тож суму звіряємо зі списком, а не віримо.
    raw = callback.data.removeprefix("support:")
    if not raw.isdigit() or int(raw) not in AMOUNTS:
        await callback.answer(BAD_AMOUNT, show_alert=True)
        return

    await callback.answer()
    if callback.message is None:
        return

    amount = int(raw)
    await callback.bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Підтримка трекера звичок",
        description=f"Добровільний донат: {amount} ⭐",
        payload=f"{PAYLOAD_PREFIX}{amount}",
        currency=STARS,
        prices=[LabeledPrice(label="Донат", amount=amount)],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    """Останнє слово бота перед списанням.

    Перевіряємо, що рахунок справді наш і не підмінений: валюта Stars,
    сума зі списку й збігається з тією, що в payload. Відмова тут —
    єдиний момент, коли гроші ще не списані.
    """
    amount = parse_payload(query.invoice_payload)
    if query.currency != STARS or amount is None or query.total_amount != amount:
        await query.answer(ok=False, error_message=BAD_AMOUNT)
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment = message.successful_payment
    # charge_id — єдиний спосіб потім повернути кошти (refundStarPayment).
    # Поки донати не пишуться в базу, журнал бота — місце, де його шукати.
    logging.info(
        "Донат: user=%s amount=%s %s charge_id=%s",
        message.from_user.id if message.from_user else None,
        payment.total_amount,
        payment.currency,
        payment.telegram_payment_charge_id,
    )
    await message.answer(THANKS.format(amount=payment.total_amount))
