"""Натискання кнопки звички: відмітити або скасувати відмітку."""

from datetime import date

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.api import HabitsAPI
from bot.keyboards import HabitCallback
from bot.new_habit import NewHabit
from bot.views import habits_view

router = Router(name="checkin")

MID_DIALOG = (
    "Спершу завершимо створення звички.\n"
    "Надішли /cancel, якщо передумав."
)


@router.callback_query(HabitCallback.filter())
async def toggle_checkin(
    callback: CallbackQuery, callback_data: HabitCallback, api: HabitsAPI, state: FSMContext
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
    # у тому числі посеред діалогу створення. Без цієї перевірки натискання
    # спрацьовувало б і малювало ЗВИЧАЙНИЙ список — найсильніший можливий
    # сигнал «діалог завершено» — хоча FSM насправді лишається на кроці
    # NewHabit.name чи .description. Наступне ж случайне повідомлення
    # людини («а коли вечеря») тихо стає назвою чи описом нової звички.
    # Той самий принцип, що вже застосований до команд у new_habit.py.
    current_state = await state.get_state()
    if current_state in (NewHabit.name.state, NewHabit.description.state):
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
        # Знімаємо саме date.today(). Спокусливо було б спитати сервер,
        # який день він вважає сьогоднішнім (у показниках є last_day) —
        # але last_day це max() з усіх відміток БЕЗ обмеження зверху,
        # а API приймає й майбутні дати. Якщо людина у браузері відмітила
        # день наперед, last_day вкаже на нього — і ми видалили б не ту
        # відмітку. Бот і API працюють на одній машині з одним годинником,
        # тож date.today() тут і простіший, і безпечніший.
        removed = await api.undo_check_in(
            telegram_id, callback_data.habit_id, date.today()
        )
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
