"""Хто саме прийшов із запитом.

Схема навмисно проста, бо клієнтів у нас рівно два й обидва свої:

1. Бот. Він — сервер, йому можна довірити спільний пароль. До кожного
   запиту він додає два заголовки: X-Telegram-Id (кого обслуговує)
   і X-Bot-Secret (доказ, що це справді наш бот). Без другого перший
   нічого не вартий: інакше будь-хто написав би "я користувач 12345"
   і прочитав чужі звички.

2. Браузер на твоєму комп'ютері. Заголовків не шле взагалі — і отримує
   окремого локального користувача. Це свідоме спрощення: вебінтерфейс
   розрахований на запуск на localhost, для однієї людини.

   ВАЖЛИВО, якщо колись виставлятимеш API назовні: локальний режим
   треба буде або вимкнути, або закрити справжнім входом із паролем.
   Зараз він означає "хто дістався до порту — той і господар".
"""

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import ALLOW_LOCAL_USER, BOT_SECRET, SESSION_SECRET, SESSION_TTL_SECONDS
from database import get_session
from models import User

# Ім'я користувача вебінтерфейсу — того, у кого telegram_id порожній.
LOCAL_USER_NAME = "Локальний користувач"

# Назва cookie із сесією.
SESSION_COOKIE = "habits_session"


# ---------- сесії у браузері ----------
#
# Сесія тут БЕЗ таблиці в базі: усе потрібне лежить у самій cookie,
# а від підробки її захищає підпис. Такий підхід називають stateless-
# сесією. Плюс — не треба ні таблиці, ні прибирання протухлих записів;
# мінус — сесію не можна відкликати достроково (доки не мине термін
# або не зміниться SESSION_SECRET). Для трекера звичок обмін чесний.


def make_session(user_id: int) -> str:
    """Зібрати вміст cookie: хто, доки, і підпис.

    Формат: user_id.термін.підпис — наприклад "7.1790000000.a3f9...".
    Перші дві частини відкриті (їх видно будь-кому), і це нормально:
    таємниці в них немає. Захищає підпис — без SESSION_SECRET підібрати
    його неможливо, тож дописати собі чужий user_id не вийде.
    """
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{user_id}.{expires_at}"
    return f"{payload}.{_sign(payload)}"


def read_session(cookie: str | None) -> int | None:
    """Дістати user_id з cookie, або None, якщо їй не можна вірити."""
    if not cookie or not SESSION_SECRET:
        return None

    parts = cookie.split(".")
    if len(parts) != 3:
        return None

    user_id_raw, expires_raw, signature = parts

    # Спершу підпис, і лише потім усе інше. Порядок навмисний: поки
    # підпис не перевірено, вміст cookie — це рядок від невідомо кого,
    # і робити з ним щось складніше за порівняння не варто.
    #
    # Порівнюємо БАЙТИ, а не рядки. Причина не в стилі: compare_digest
    # на рядках із не-ASCII символами кидає TypeError. Cookie ж повністю
    # у руках того, хто її надсилає, тож кирилиця в підписі — це не
    # екзотика, а найпростіший спосіб зронити сервер у 500. У байтах
    # такої проблеми немає: там будь-який вміст просто байти.
    expected = _sign(f"{user_id_raw}.{expires_raw}").encode()
    if not hmac.compare_digest(expected, signature.encode()):
        return None

    try:
        user_id, expires_at = int(user_id_raw), int(expires_raw)
    except ValueError:
        return None

    if expires_at < time.time():
        return None

    return user_id


def _sign(payload: str) -> str:
    """HMAC-SHA256 від вмісту cookie на основі SESSION_SECRET.

    Чому HMAC, а не просто хеш: звичайний sha256(секрет + дані) уразливий
    до атаки подовженням довжини — маючи хеш, можна дописати даних і
    порахувати новий, не знаючи секрету. HMAC збудований так, що цей
    трюк не працює, і саме тому для підпису беруть його, а не хеш.
    """
    return hmac.new(
        SESSION_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def get_or_create_user(
    session: Session, telegram_id: int | None, name: str
) -> User:
    """Знайти користувача за telegram_id, а якщо його ще немає — завести."""
    # == None у SQLAlchemy перетворюється на SQL IS NULL, але is_(None)
    # каже те саме прямо й не збиває з пантелику лінтери.
    condition = (
        User.telegram_id.is_(None)
        if telegram_id is None
        else User.telegram_id == telegram_id
    )

    user = session.exec(select(User).where(condition)).first()
    if user is not None:
        return user

    user = User(telegram_id=telegram_id, name=name)
    session.add(user)

    try:
        session.commit()
    except IntegrityError:
        # Сюди потрапляємо, якщо два запити від однієї людини прийшли
        # майже одночасно і обидва не побачили її в базі. Один запис
        # пройшов, другий уперся в unique-обмеження. Це не помилка —
        # база просто вберегла нас від дубліката. Відкочуємось
        # і забираємо той запис, який створив швидший запит.
        session.rollback()
        return session.exec(select(User).where(condition)).one()

    session.refresh(user)
    return user


def get_current_user(
    session: Session = Depends(get_session),
    # FastAPI сам перетворює ім'я аргументу на назву заголовка:
    # x_telegram_id -> X-Telegram-Id. Тип int означає, що нечислове
    # значення буде відхилено ще до входу в цю функцію.
    x_telegram_id: int | None = Header(default=None),
    x_bot_secret: str | None = Header(default=None),
    habits_session: str | None = Cookie(default=None),
) -> User:
    """Визначити користувача запиту. Використовується в кожному ендпоінті.

    Способів упізнати три, і перевіряються вони саме в цьому порядку:

    1. Cookie сесії — браузер, який уже увійшов через бота.
    2. Заголовки бота — сам бот, від імені конкретної людини.
    3. Локальний режим — запит зовсім без доказів. Раніше так працював
       увесь вебінтерфейс; тепер це лише зручність для розробки, і за
       замовчуванням він ВИМКНЕНИЙ (див. ALLOW_LOCAL_USER у config.py).

    Cookie попереду заголовків не через більшу довіру, а тому що це
    різні клієнти: cookie шле браузер, заголовки — бот, і одночасно
    вони не приходять.
    """
    user_id = read_session(habits_session)
    if user_id is not None:
        user = session.get(User, user_id)
        if user is not None:
            return user
        # Підпис правильний, а користувача немає — його видалили вже
        # після видачі cookie. Не помилка: просто йдемо далі, ніби
        # cookie й не було.

    if x_telegram_id is None:
        if not ALLOW_LOCAL_USER:
            raise HTTPException(
                status_code=401,
                detail="Потрібен вхід. Відкрий сторінку застосунку і "
                       "увійди через Telegram.",
            )
        return get_or_create_user(session, None, LOCAL_USER_NAME)

    # Далі — запит нібито від бота, і його треба перевірити.

    if not BOT_SECRET:
        # Пароль не налаштовано. Мовчки пускати не можна: порівняння
        # порожнього рядка з порожнім дало б True, і доступ відкрився б
        # усім охочим. Тому доступ ботові просто закритий, поки в .env
        # не з'явиться BOT_SECRET.
        raise HTTPException(
            status_code=503,
            detail="Доступ для бота не налаштовано: у .env немає BOT_SECRET",
        )

    # compare_digest замість звичайного ==, бо == порівнює рядки посимвольно
    # й обривається на першій розбіжності. Різниця в часі мікроскопічна,
    # але за нею можна підбирати пароль по одному символу. compare_digest
    # витрачає однаковий час незалежно від того, де саме розбіжність.
    if x_bot_secret is None or not secrets.compare_digest(x_bot_secret, BOT_SECRET):
        raise HTTPException(status_code=401, detail="Невірний секрет бота")

    # Ім'я порожнє навмисно: справжнє ім'я приходить окремим запитом
    # із тілом JSON. Кирилицю не можна класти в HTTP-заголовок —
    # заголовки за стандартом однобайтові, і "Олена" їх ламає.
    return get_or_create_user(session, x_telegram_id, "")


# Коротке ім'я, щоб не писати Depends(get_current_user) у кожному ендпоінті.
# Так само влаштований SessionDep у main.py.
CurrentUser = Annotated[User, Depends(get_current_user)]


def require_bot(x_bot_secret: str | None = Header(default=None)) -> None:
    """Перевірка «це справді бот» — без прив'язки до конкретного користувача.

    get_current_user завжди відповідає на питання "від імені кого цей
    запит" — саме для цього й потрібен X-Telegram-Id. Але для операцій,
    які стосуються ВСІХ telegram-користувачів одразу (розсилка
    нагадувань — "дай мені список усіх, кому маю написати"), поняття
    "поточний користувач" просто немає. Тому окрема, простіша перевірка:
    той самий секрет, але без заголовка з id.
    """
    if not BOT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Доступ для бота не налаштовано: у .env немає BOT_SECRET",
        )

    if x_bot_secret is None or not secrets.compare_digest(x_bot_secret, BOT_SECRET):
        raise HTTPException(status_code=401, detail="Невірний секрет бота")


# None замість User: цій залежності нема що повертати, вона лише
# дозволяє або забороняє прохід. FastAPI все одно виконає перевірку.
RequireBot = Annotated[None, Depends(require_bot)]
