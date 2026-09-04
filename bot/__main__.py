"""Запуск бота:  python -m bot

Файл із назвою __main__.py усередині пакета — це те, що виконується
командою python -m ім'я_пакета. Зручно: одна точка входу, і не треба
памʼятати шлях до конкретного скрипта.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent

from bot import checkin, login, manage, menu, new_habit
from bot.api import ApiError, HabitsAPI
from bot.reminders import reminder_loop
from config import API_URL, BOT_SECRET, BOT_TOKEN, REMINDER_HOUR

COMMANDS = [
    BotCommand(command="habits", description="Список звичок"),
    BotCommand(command="new", description="Додати звичку"),
    BotCommand(command="manage", description="Керувати звичками"),
    BotCommand(command="help", description="Довідка"),
]


def check_settings() -> None:
    """Не стартувати без налаштувань, пояснивши, чого саме бракує.

    Без цієї перевірки бот упав би десь усередині aiogram зі стеком
    викликів, з якого причина «забув заповнити .env» зовсім не очевидна.
    """
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN — токен від @BotFather")
    if not BOT_SECRET:
        problems.append("BOT_SECRET — спільний пароль бота й API")
    elif not BOT_SECRET.isascii():
        # HTTP-заголовки за стандартом однобайтові (про це вже двічі
        # попереджено коментарями в conftest.py та bot/api.py — там ідеться
        # про ім'я користувача). Секрет — те саме поле ризику, тільки
        # тут на кону не одне привітання, а взагалі кожна дія бота: секрет
        # їде в заголовок X-Bot-Secret щоразу. Кирилиця в ньому не дає
        # зрозумілої помилки — httpx падає з голим UnicodeEncodeError ще
        # ДО відправки запиту, і людина бачить лише "Щось пішло не так"
        # без жодного натяку на причину. Перевірка на порожнечу цього не
        # ловить: непорожній кириличний секрет проходить її мовчки.
        problems.append("BOT_SECRET — лише латиниця й цифри (кирилиця в HTTP-заголовок не влазить)")

    if problems:
        print("Бот не може стартувати. У файлі .env бракує:\n")
        for problem in problems:
            print(f"  • {problem}")
        print("\nЗразок лежить у .env.example — скопіюй його в .env.")
        print('Згенерувати секрет:  python -c "import secrets; '
              'print(secrets.token_hex(32))"')
        sys.exit(1)


async def on_error(event: ErrorEvent, api: HabitsAPI) -> bool:
    """Один обробник помилок на весь бот.

    Без нього кожен виняток доводилося б ловити в кожному обробнику
    окремим try/except — і половину все одно забули б. Тут ми ловимо
    все в одному місці: людині показуємо зрозумілий текст, а повний
    стек лишаємо в логах для себе.
    """
    if isinstance(event.exception, ApiError):
        # ApiUnavailable — теж ApiError (успадкований клас), тому ця
        # гілка ловить обидва. api.py дбайливо розрізняє причину —
        # "Чи запущений uvicorn?" для обриву з'єднання, "не відповів
        # вчасно" для таймауту, текст помилки для іншого — і раніше тут
        # стояв ОДИН заклинений рядок, що затирав усі три власним
        # текстом. Найгірше — той самий "Запусти uvicorn", коли сервер
        # живий, просто повільний: людина йде лагодити не те, що зламане.
        text = str(event.exception)
    else:
        logging.exception("Необроблена помилка", exc_info=event.exception)
        text = "Щось пішло не так. Спробуй ще раз."

    # Дістати чат, у якому все сталося: подія могла прийти
    # і повідомленням, і натисканням кнопки.
    update = event.update
    try:
        if update.callback_query is not None:
            # Знімаємо «годинник» із кнопки, інакше вона так і зависне.
            #
            # try тут не зайвий: обробник міг уже відповісти на це
            # натискання ДО того, як стався збій (саме так влаштований
            # toggle_checkin — він підтверджує кнопку, а потім малює
            # список). Друга відповідь на той самий callback — помилка
            # від Telegram, і без перехоплення вона вилетіла б уже
            # з самого обробника помилок.
            await update.callback_query.answer(text[:200], show_alert=True)
        elif update.message is not None:
            await update.message.answer(text)
    except TelegramAPIError:
        logging.warning("Не вдалося показати користувачеві текст помилки")

    # True означає «помилку опрацьовано» — aiogram не валить бота.
    return True


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    check_settings()

    bot = Bot(
        token=BOT_TOKEN,
        # parse_mode на весь бот, щоб не вказувати його в кожному
        # повідомленні. Саме тому <b>жирний</b> у текстах працює.
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # MemoryStorage тримає стан діалогів в оперативній памʼяті.
    # Наслідок: перезапуск бота обриває незавершені діалоги створення
    # звички. Для навчального проєкту це прийнятно; у продакшені
    # ставлять RedisStorage, щоб стан переживав перезапуск.
    dispatcher = Dispatcher(storage=MemoryStorage())

    api = HabitsAPI()

    # Кладемо клієнт у диспетчер — і aiogram сам передаватиме його
    # в кожен обробник, який попросить аргумент з назвою api.
    # Той самий принцип, що Depends у FastAPI, тільки за назвою.
    dispatcher["api"] = api

    dispatcher.errors.register(on_error)

    # Порядок має значення: aiogram перебирає роутери зверху вниз
    # і віддає подію першому, чий фільтр підійшов.
    #
    # login — найперший: він ловить лише /start З КОДОМ і має встигнути
    # перехопити його раніше, ніж menu відповість звичайним привітанням.
    #
    # Далі обидва роутери з діалогами (new_habit, manage) — усередині
    # діалогу їхні обробники мають перехопити текст раніше, ніж menu
    # спробує розпізнати в ньому команду.
    #
    # checkin — останній: у ньому лежить catch-all для «нічийних»
    # натискань, і він за визначенням має бачити подію лише тоді,
    # коли її не забрав ніхто інший.
    dispatcher.include_router(login.router)
    dispatcher.include_router(new_habit.router)
    dispatcher.include_router(manage.router)
    dispatcher.include_router(menu.router)
    dispatcher.include_router(checkin.router)

    # Усе, що може кинути виняток, — усередині try. Перевірка токена
    # відбувається саме на get_me(): з невірним токеном він падає,
    # і якби це сталося ДО try, ні httpx-клієнт, ні сесія бота не
    # закрилися б — Python лаявся б на незакриті зʼєднання.
    try:
        await bot.set_my_commands(COMMANDS)

        me = await bot.get_me()
        logging.info(
            "Бот @%s запущений. API: %s. Нагадування о %d:00",
            me.username, API_URL, REMINDER_HOUR,
        )

        # Нагадування живуть в окремій задачі, паралельно з polling —
        # інакше цикл reminder_loop (він спить годинами) заблокував би
        # весь бот: жоден update не оброблявся б, поки той не прокинеться.
        reminders = asyncio.create_task(reminder_loop(bot, api))

        try:
            # Polling: бот сам питає Telegram «чи є новини?». Простий
            # спосіб, не потребує ні HTTPS, ні білої IP-адреси — тому
            # ідеальний для розробки. На сервері зазвичай переходять
            # на webhook, де вже Telegram стукає до нас.
            #
            # drop_pending_updates=True викидає повідомлення, що
            # назбиралися поки бот лежав: інакше після кожного
            # перезапуску він відповідав би на старі команди, збиваючи
            # людей з пантелику.
            await dispatcher.start_polling(bot, drop_pending_updates=True)
        finally:
            # Скасовуємо задачу нагадувань і чекаємо на її завершення.
            # Без await після cancel() задача могла б не встигнути
            # обробити CancelledError до того, як процес почне
            # закриватися, — і Python поскаржився б на незавершену
            # задачу в логах.
            reminders.cancel()
            try:
                await reminders
            except asyncio.CancelledError:
                pass
    finally:
        # Закриваємо обидва зʼєднання, навіть якщо бот падає.
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Ctrl+C — це не аварія, а звичайний спосіб зупинити бота.
        # Без цього рядка консоль щоразу засмічувалася б стеком викликів.
        print("\nБот зупинений.")
