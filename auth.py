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

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config import BOT_SECRET
from database import get_session
from models import User

# Ім'я користувача вебінтерфейсу — того, у кого telegram_id порожній.
LOCAL_USER_NAME = "Локальний користувач"


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
) -> User:
    """Визначити користувача запиту. Використовується в кожному ендпоінті."""
    if x_telegram_id is None:
        # Заголовків немає — це браузер, локальний режим.
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
