"""Натискання кнопки звички: відмітити або скасувати відмітку."""

from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api import HabitsAPI
from bot.keyboards import (
    HabitCallback,
    ReminderCallback,
    reminder_habit_ids,
    reminder_keyboard,
)
from bot.reminders import reminder_text
from bot.views import habits_view

router = Router(name="checkin")

MID_DIALOG = "Спершу завершимо почате.\nНадішли /cancel, якщо передумав."


# F.action == "toggle", а не просто HabitCallback.filter(): у кнопки звички
# тепер кілька різних дій (відмітити, відкрити картку, перейменувати,
# видалити). Без явної дії цей обробник ковтав би їх усі — а він уміє
# лише перемикати відмітку.
@router.callback_query(HabitCallback.filter(F.action == "toggle"))
async def toggle_checkin(
    callback: CallbackQuery,
    callback_data: HabitCallback,
    api: HabitsAPI,
    state: FSMContext,
) -> None:
    """Перемкнути стан звички на сьогодні.

    Логіка перемикача навмисно побудована «через спробу»: спершу пробуємо
    відмітити, і якщо API відповів «уже відмічено» — знімаємо відмітку.

    Так надійніше, ніж спершу питати стан, а потім діяти: між питанням
    і дією стан міг би змінитися (наприклад, людина відмітила ту саму
    звичку у браузері), і ми зробили б не те, що видно на екрані.
    Тут же рішення ухвалює сама база, і розбіжності не виникає.
    """
    if callback.from_user is None:
        return

    telegram_id = callback.from_user.id

    # Кнопка звички не привʼязана до жодного стану — вона працює завжди,
    # у тому числі посеред діалогу. Без цієї перевірки натискання
    # спрацьовувало б і малювало ЗВИЧАЙНИЙ список — найсильніший можливий
    # сигнал «діалог завершено» — хоча FSM насправді лишається на кроці
    # введення. Наступне ж випадкове повідомлення людини («а коли вечеря»)
    # тихо стало б назвою звички.
    #
    # Перевіряємо «є БУДЬ-ЯКИЙ стан», а не перелік конкретних: діалогів
    # у боті вже два (створення й редагування), і перелік довелося б
    # доповнювати щоразу — а забути легко. Жоден стан цього бота не
    # передбачає відмічання посеред нього, тож умова чесно описує намір.
    if await state.get_state() is not None:
        await callback.answer(MID_DIALOG, show_alert=True)
        return

    created = await api.check_in(telegram_id, callback_data.habit_id)

    if created:
        note = "Відмічено 🔥"
    else:
        # Сюди потрапляємо лише після 409, а 409 сервер віддає вже ПІСЛЯ
        # перевірки, що звичка існує і належить цій людині. Тобто тут
        # гарантовано: звичка наша і сьогодні відмічене.
        #
        # API визначає день у часовому поясі власника. Годинник бота
        # може показувати іншу дату, а last_day — майбутню відмітку.
        today = await api.today(telegram_id)
        removed = await api.undo_check_in(telegram_id, callback_data.habit_id, today)
        # Не оголошуємо успіх наосліп: якщо відмітку встиг зняти хтось
        # інший (той самий акаунт у браузері), чесніше сказати правду.
        note = "Відмітку знято" if removed else "Відмітку вже було знято"

    # Дані для перемальовування збираємо ДО того, як відповісти на
    # callback — навмисно, а не одразу після check_in/undo_check_in.
    # Якщо habits_view() впаде (обрив саме зараз), callback ще НІ РАЗУ
    # не отримав відповіді, і on_error спокійно відповість на нього сам,
    # одним викликом. У зворотному порядку callback уже мав би відповідь
    # "Відмічено", і другий виклик answer() з тексту помилки мусив би
    # покладатися на те, чи Telegram узагалі дозволяє відповісти вдруге —
    # а це недокументована поведінка, на яку краще не покладатись.
    text, keyboard = await habits_view(api, telegram_id)

    # Спливаюча підказка вгорі екрана. Заразом це знімає «годинник»
    # на кнопці: поки не відповісти на callback, Telegram показує
    # користувачеві, що бот думає. Пропустити цей виклик — типова
    # помилка новачка: бот працює, а виглядає зависшим.
    await callback.answer(note)

    # Повідомлення, до якого причеплена кнопка, буває недоступним:
    # Telegram не віддає боту вміст повідомлень, старших за добу.
    # Тоді редагувати нічого — просто надсилаємо свіжий список.
    if not isinstance(callback.message, Message):
        if callback.message is not None:
            await callback.bot.send_message(
                callback.message.chat.id, text, reply_markup=keyboard
            )
        return

    # Перемальовуємо ТЕ САМЕ повідомлення, а не шлемо нове. Інакше
    # після десяти відміток чат перетворюється на стрічку копій списку.
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as error:
        # Telegram відмовляється зберігати повідомлення, якщо воно
        # анітрохи не змінилося. Для нас це не проблема: на екрані
        # і так уже те, що треба.
        if "message is not modified" not in str(error):
            raise


@router.callback_query(ReminderCallback.filter())
async def check_in_from_reminder(
    callback: CallbackQuery, callback_data: ReminderCallback, api: HabitsAPI
) -> None:
    """Відмітка прямо з нагадування — за день, про який воно нагадувало.

    На відміну від toggle_checkin, тут лише ВІДМІЧАЄМО, а не перемикаємо:
    нагадування просить «зроби», і випадковий другий дотик не має
    мовчки знімати щойно поставлену відмітку. Зняти — у /habits.

    Перевірки «посеред діалогу» тут немає навмисно: toggle_checkin
    відмовляє, бо малює звичайний список, який виглядає як кінець
    діалогу. Нагадування ж перемальовується саме в себе — жодного
    хибного сигналу людині, тож і забороняти нічого.
    """
    if callback.from_user is None:
        return

    try:
        day = date.fromisoformat(callback_data.day)
    except ValueError:
        # Дані кнопки шле клієнт — модифікований може вписати що завгодно.
        await callback.answer("Ця кнопка вже неактуальна. Онови список: /habits")
        return

    telegram_id = callback.from_user.id
    # API приймає відмітку й за майбутній день. Нагадування надсилається
    # лише за сьогодні, тож пізніша дата — тільки від підробленого клієнта.
    if day > await api.today(telegram_id):
        await callback.answer("Ця кнопка вже неактуальна. Онови список: /habits")
        return

    created = await api.check_in(telegram_id, callback_data.habit_id, day)
    note = "Відмічено 🔥" if created else "Уже відмічено ✅"

    message = callback.message
    if not isinstance(message, Message):
        # Старе повідомлення (понад 48 год) Telegram боту не віддає —
        # відмітка вже збережена, перемальовувати просто нічого.
        await callback.answer(note)
        return

    # Звичка тепер відмічена в будь-якому разі (щойно чи раніше), тож її
    # кнопку прибираємо. Що лишилось у клавіатурі — те й досі не відмічено.
    remaining_ids = [
        habit_id
        for habit_id in reminder_habit_ids(message.reply_markup)
        if habit_id != callback_data.habit_id
    ]
    # Повні назви беремо з API, а не з кнопок: там вони вкорочені до 40
    # символів. Звички, яку тим часом видалили, у списку просто не буде.
    habits = {habit["id"]: habit for habit in await api.list_habits(telegram_id)}
    remaining = [habits[i] for i in remaining_ids if i in habits]

    # Відповідаємо на callback ПІСЛЯ збору даних — з тієї ж причини, що
    # й у toggle_checkin: якщо list_habits впаде, on_error відповість сам.
    await callback.answer(note)

    try:
        await message.edit_text(
            reminder_text([habit["name"] for habit in remaining]),
            reply_markup=reminder_keyboard(remaining, day),
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


# Реєструється ОСТАННІМ в останньому роутері — тобто спрацьовує лише
# тоді, коли кнопку не забрав ніхто інший.
@router.callback_query()
async def stale_button(callback: CallbackQuery) -> None:
    """Кнопка, яку більше нікому обробляти.

    Найчастіший випадок: людина гортає чат нагору й тисне «Пропустити»
    зі старого, давно завершеного діалогу створення звички. Той обробник
    працює лише всередині свого стану, тож натискання нікуди не потрапляє.

    А кожен callback ОБОВʼЯЗКОВО треба підтвердити — інакше Telegram
    крутить годинник на кнопці, поки не мине таймаут. Мовчазна кнопка
    виглядає як зависший бот, тому ця «заглушка» тут не для краси.
    """
    await callback.answer("Ця кнопка вже неактуальна. Онови список: /habits")
