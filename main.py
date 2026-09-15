"""Трекер звичок: ендпоінти API та віддача сторінки застосунку."""

import secrets
import asyncio
import logging
import math
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, update, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

import stats
from auth import (
    SESSION_COOKIE,
    CurrentUser,
    RequireBot,
    make_session,
)
from calendar_rules import local_day, now_utc
from config import (
    BACKUP_DIR,
    BOT_USERNAME,
    LOGIN_CODE_RATE_LIMIT,
    LOGIN_CODE_RATE_WINDOW_SECONDS,
    LOGIN_TOKEN_TTL_SECONDS,
    SESSION_SECRET,
    SESSION_TTL_SECONDS,
    SESSION_COOKIE_SECURE,
)
from database import create_db_and_tables, get_session
from rate_limit import RateLimiter
from models import (
    Checkin,
    CheckinCreate,
    Habit,
    HabitCreate,
    HabitPublic,
    HabitStats,
    HabitUpdate,
    LoginToken,
    ReminderSent,
    User,
    UserPublic,
    UserUpdate,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Код до yield виконується на старті сервера, після yield — на зупинці."""
    create_db_and_tables()
    backup_task = asyncio.create_task(backup_loop()) if BACKUP_DIR else None
    try:
        yield
    finally:
        if backup_task:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass


async def backup_loop():
    """Одна узгоджена копія на день; збої видимі в журналі сервера."""
    from backups import daily_backup
    from database import engine

    while True:
        try:
            await asyncio.to_thread(daily_backup, engine, BACKUP_DIR)
        except Exception:
            logging.exception("Не вдалося створити щоденну резервну копію")
        await asyncio.sleep(3600)


app = FastAPI(title="Трекер звичок", lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]


def user_today(user: User) -> date:
    return local_day(user.timezone)


@app.get("/health")
def health(session: SessionDep) -> dict:
    try:
        session.exec(select(1)).one()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="База даних недоступна") from None
    return {"status": "ok"}


def find_checkin(habit_id: int, day: date, session: Session) -> Checkin | None:
    """Знайти відмітку звички за конкретний день, або None.

    Потрібна двом ендпоінтам: створення перевіряє нею дублікат,
    видалення — наявність.
    """
    return session.exec(
        select(Checkin).where(Checkin.habit_id == habit_id, Checkin.day == day)
    ).first()


def get_habit_or_404(habit_id: int, user: User, session: Session) -> Habit:
    """Знайти звичку СЕРЕД ЗВИЧОК ЦЬОГО КОРИСТУВАЧА або відповісти 404.

    Ця перевірка потрібна майже в кожному ендпоінті, тож винесена окремо,
    щоб не копіювати ті самі три рядки шість разів.

    Чужа звичка дає 404 «не знайдено», а не 403 «заборонено» — навмисно.
    403 означало б «така звичка існує, але не твоя», і перебором id
    можна було б дізнатися, скільки звичок у сусіда. 404 не каже нічого.
    """
    habit = session.get(Habit, habit_id)
    if habit is None or habit.user_id != user.id:
        raise HTTPException(status_code=404, detail="Звичку не знайдено")
    return habit


# ---------- Вхід із браузера через бота ----------
#
# Потік цілком: браузер просить код -> показує посилання на бота ->
# людина відкриває його у своєму Telegram і підтверджує -> бот каже API
# «код такий-то належить мені» -> браузер обмінює код на cookie сесії.
#
# Суть у тому, що браузер не знає, хто його власник у Telegram, а бот
# знає напевно. Код — це естафетна паличка між ними.


# Один лімітер на весь процес: лічильники мають переживати окремі запити.
login_code_limiter = RateLimiter(
    limit=LOGIN_CODE_RATE_LIMIT, window_seconds=LOGIN_CODE_RATE_WINDOW_SECONDS
)


@app.post("/auth/login-code", status_code=201)
def create_login_code(
    request: Request, session: SessionDep, response: Response
) -> dict:
    """Видати браузеру одноразовий код і посилання на бота."""
    if not SESSION_SECRET or not BOT_USERNAME:
        raise HTTPException(
            status_code=503,
            detail="Вхід не налаштовано: у .env потрібні SESSION_SECRET і BOT_USERNAME",
        )

    # Перевірка ДО запису в базу — інакше ліміт не захищав би саме те,
    # заради чого він існує. Ключ — IP-адреса: до входу іншого
    # способу відрізнити одного відвідувача від іншого немає.
    client_ip = request.client.host if request.client else "unknown"
    retry_after = login_code_limiter.hit(client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Забагато спроб входу. Зачекайте хвилину й спробуйте ще раз.",
            # Retry-After — стандартний заголовок: скільки секунд чекати.
            # Цілі секунди вгору, щоб клієнт не повернувся на мить раніше.
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )

    # token_urlsafe(32) — 32 випадкові байти. Підібрати перебором
    # неможливо, а саме на це й покладається безпека всього потоку.
    token = secrets.token_urlsafe(32)
    session.execute(
        delete(LoginToken).where(
            LoginToken.created_at
            < datetime.now() - timedelta(seconds=LOGIN_TOKEN_TTL_SECONDS),
        )
    )
    session.add(LoginToken(token=token))
    session.commit()
    response.headers["Cache-Control"] = "no-store"

    return {
        "token": token,
        "url": f"https://t.me/{BOT_USERNAME}?start={token}",
        "expires_in": LOGIN_TOKEN_TTL_SECONDS,
    }


@app.post("/auth/confirm")
def confirm_login_code(
    data: dict, user: CurrentUser, _: RequireBot, session: SessionDep
) -> dict:
    """Бот підтверджує код від імені людини, яка його відкрила.

    Захищено і RequireBot, і CurrentUser: секрет доводить, що це наш бот,
    а X-Telegram-Id каже, кому саме прив'язати код. Обидва потрібні —
    без другого бот міг би підтвердити код, але не було б кого записати.
    """
    token = str(data.get("token", ""))
    result = session.execute(
        update(LoginToken)
        .where(
            LoginToken.token == token,
            LoginToken.created_at
            >= datetime.now() - timedelta(seconds=LOGIN_TOKEN_TTL_SECONDS),
            or_(LoginToken.user_id.is_(None), LoginToken.user_id == user.id),
        )
        .values(user_id=user.id)
    )
    session.commit()
    if result.rowcount == 0:
        login = session.get(LoginToken, token)
        if login is None or _is_expired(login):
            raise HTTPException(
                status_code=404, detail="Код недійсний або протермінований"
            )
        raise HTTPException(
            status_code=409, detail="Код уже підтверджено іншим користувачем"
        )
    return {"status": "confirmed"}


@app.get("/auth/login-code/{token}")
def poll_login_code(token: str, response: Response, session: SessionDep) -> dict:
    """Браузер питає: код уже підтверджено? Якщо так — видати сесію.

    Тут код і згорає: після обміну запис видаляється, тож повторно
    тим самим кодом сесію не отримати. Це важливо саме тому, що код
    видно в адресному рядку Telegram і він може лишитись в історії.
    """
    # DELETE ... RETURNING є однією атомарною операцією: лише запит,
    # який справді забрав код, може видати cookie. Два SELECT перед
    # двома DELETE видавали б дві сесії з одного коду.
    cutoff = datetime.now() - timedelta(seconds=LOGIN_TOKEN_TTL_SECONDS)
    user_id = session.execute(
        delete(LoginToken)
        .where(
            LoginToken.token == token,
            LoginToken.user_id.is_not(None),
            LoginToken.created_at >= cutoff,
        )
        .returning(LoginToken.user_id)
    ).scalar_one_or_none()
    session.commit()
    response.headers["Cache-Control"] = "no-store"

    if user_id is None:
        login = session.get(LoginToken, token)
        if login is None:
            raise HTTPException(status_code=404, detail="Код недійсний")
        if login.created_at < cutoff:
            session.execute(delete(LoginToken).where(LoginToken.token == token))
            session.commit()
            raise HTTPException(status_code=410, detail="Код протермінований")
        return {"status": "pending"}

    response.set_cookie(
        SESSION_COOKIE,
        make_session(user_id),
        max_age=SESSION_TTL_SECONDS,
        # httponly — cookie недосяжна для JavaScript. Якщо на сторінку
        # колись просочиться чужий скрипт, він не зможе її вкрасти.
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        # samesite=lax — браузер не надішле cookie на запити з чужих
        # сайтів, що прибирає цілий клас атак (CSRF).
        samesite="lax",
    )
    return {"status": "confirmed"}


@app.post("/auth/logout")
def logout(response: Response) -> dict:
    """Вийти: прибрати cookie.

    Сама сесія при цьому лишається технічно дійсною до кінця терміну —
    так влаштовані підписані cookie без таблиці в базі. Для трекера
    звичок прийнятно; там, де це не так, тримають список сесій у базі.
    """
    response.delete_cookie(SESSION_COOKIE)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "logged_out"}


def _is_expired(login: LoginToken) -> bool:
    return login.created_at < datetime.now() - timedelta(
        seconds=LOGIN_TOKEN_TTL_SECONDS
    )


# ---------- Користувач ----------


@app.get("/users/me")
def read_me(user: CurrentUser) -> UserPublic:
    """Хто я з погляду API. Бот викликає це, щоб перевірити зв'язок.

    Повертаємо об'єкт User, а в анотації стоїть UserPublic — і FastAPI
    сам відкидає все зайве. Так само працюють ендпоінти звичок:
    вони віддають Habit, а назовні виходить HabitPublic без user_id.
    """
    return user


@app.patch("/users/me")
def update_me(data: UserUpdate, user: CurrentUser, session: SessionDep) -> UserPublic:
    """Зберегти ім'я користувача (бот надішле його при /start)."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@app.get("/users/me/today")
def read_today(user: CurrentUser, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"day": user_today(user).isoformat()}


@app.get("/users/me/export")
def export_history(user: CurrentUser, session: SessionDep) -> JSONResponse:
    # sqlite3 у стандартному режимі не починає транзакцію для SELECT.
    # Один snapshot не дозволяє одночасним змінам роз'єднати звички
    # та їхні відмітки в експорті.
    connection = session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN")
    session.refresh(user)
    habits = session.exec(
        select(Habit).where(Habit.user_id == user.id).order_by(Habit.id)
    ).all()
    checkins = session.exec(
        select(Checkin)
        .join(Habit)
        .where(Habit.user_id == user.id)
        .order_by(Checkin.day, Checkin.id)
    ).all()
    return JSONResponse(
        {
            "version": 1,
            "exported_at": now_utc().isoformat(),
            "user": UserPublic.model_validate(user).model_dump(mode="json"),
            "habits": [
                HabitPublic.model_validate(habit).model_dump(mode="json")
                for habit in habits
            ],
            "checkins": [checkin.model_dump(mode="json") for checkin in checkins],
        },
        headers={
            "Content-Disposition": 'attachment; filename="habits-export.json"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/reminder-targets")
def reminder_targets(_: RequireBot, session: SessionDep) -> list[dict]:
    users = session.exec(
        select(User).where(
            User.telegram_id.is_not(None),
            User.reminders_enabled.is_(True),
        )
    ).all()
    return [
        {
            "telegram_id": user.telegram_id,
            "timezone": user.timezone,
            "reminder_hour": user.reminder_hour,
            "last_reminder_day": user.last_reminder_day,
        }
        for user in users
    ]


@app.post("/reminder-targets/{telegram_id}/sent")
def reminder_sent(
    telegram_id: int, data: ReminderSent, _: RequireBot, session: SessionDep
) -> dict:
    user = session.exec(select(User).where(User.telegram_id == telegram_id)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Користувача не знайдено")
    # Пізня відповідь за вчора не повинна стерти факт сьогоднішньої доставки.
    session.execute(
        update(User)
        .where(
            User.id == user.id,
            or_(User.last_reminder_day.is_(None), User.last_reminder_day < data.day),
        )
        .values(last_reminder_day=data.day)
    )
    session.commit()
    return {"status": "recorded"}


@app.get("/telegram-users")
def list_telegram_users(_: RequireBot, session: SessionDep) -> list[int]:
    """Усі telegram_id користувачів бота. Для щоденної розсилки нагадувань.

    Захищено RequireBot, а не CurrentUser: тут немає "поточного
    користувача" — питання не "хто ти", а "дай мені всіх, кому писати".

    Локальний користувач браузера (telegram_id=None) сюди не потрапляє —
    надсилати йому нагадування нікуди, у нього немає Telegram-акаунта.
    """
    rows = session.exec(
        select(User.telegram_id).where(User.telegram_id.is_not(None))
    ).all()
    return list(rows)


# ---------- Звички ----------


@app.get("/habits")
def list_habits(
    user: CurrentUser, session: SessionDep, include_archived: bool = False
) -> list[HabitPublic]:
    """Повернути звички поточного користувача."""
    query = select(Habit).where(Habit.user_id == user.id)
    if not include_archived:
        query = query.where(Habit.archived_at.is_(None))
    return list(session.exec(query.order_by(Habit.id)).all())


@app.get("/habits/{habit_id}")
def get_habit(habit_id: int, user: CurrentUser, session: SessionDep) -> HabitPublic:
    """Повернути одну звичку за id."""
    return get_habit_or_404(habit_id, user, session)


@app.post("/habits", status_code=201)
def create_habit(
    data: HabitCreate, user: CurrentUser, session: SessionDep
) -> HabitPublic:
    """Створити нову звичку для поточного користувача."""
    # Власник береться з автентифікації, а НЕ з тіла запиту. Якби user_id
    # приходив від клієнта, будь-хто міг би створити звичку іншій людині.
    start_date = data.start_date or user_today(user)
    if start_date > user_today(user):
        raise HTTPException(
            status_code=422, detail="Дата початку не може бути в майбутньому"
        )
    habit = Habit.model_validate(
        data, update={"user_id": user.id, "start_date": start_date}
    )
    session.add(habit)
    session.commit()
    session.refresh(habit)
    return habit


@app.patch("/habits/{habit_id}")
def update_habit(
    habit_id: int, data: HabitUpdate, user: CurrentUser, session: SessionDep
) -> HabitPublic:
    """Змінити назву та/або опис звички."""
    habit = get_habit_or_404(habit_id, user, session)

    # exclude_unset=True бере лише поля, які клієнт справді надіслав.
    changes = data.model_dump(exclude_unset=True)
    if "archived" in changes:
        archived = changes.pop("archived")
        habit.archived_at = (
            (habit.archived_at or user_today(user)) if archived else None
        )
    for field, value in changes.items():
        setattr(habit, field, value)

    session.add(habit)
    session.commit()
    session.refresh(habit)
    return habit


@app.delete("/habits/{habit_id}", status_code=204)
def delete_habit(habit_id: int, user: CurrentUser, session: SessionDep) -> None:
    """Видалити звичку разом з усіма її відмітками."""
    habit = get_habit_or_404(habit_id, user, session)

    # Спершу приберемо відмітки. Інакше в базі лишились би "сироти" —
    # відмітки, що посилаються на звичку, якої вже не існує.
    checkins = session.exec(select(Checkin).where(Checkin.habit_id == habit_id)).all()
    for checkin in checkins:
        session.delete(checkin)

    session.delete(habit)
    session.commit()


# ---------- Відмітки ----------


@app.get("/habits/{habit_id}/checkins")
def list_checkins(
    habit_id: int,
    user: CurrentUser,
    session: SessionDep,
    since: date | None = None,
    until: date | None = None,
) -> list[Checkin]:
    """Повернути відмітки звички, від найновішої до найстарішої.

    since і until — необов'язкові межі періоду. Вони не згадані у шляху,
    тому FastAPI розуміє їх як параметри рядка запиту:
        /habits/2/checkins?since=2026-06-01&until=2026-08-30
    Без них повертаються всі відмітки.
    """
    get_habit_or_404(habit_id, user, session)

    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=422, detail="Початок періоду має бути не пізніше кінця"
        )

    # where — це фільтр (SQL: WHERE habit_id = ...),
    # order_by — сортування (SQL: ORDER BY day DESC).
    query = select(Checkin).where(Checkin.habit_id == habit_id)

    # Умови додаємо по одній, лише якщо межу справді передали.
    # Запит збирається поступово — це нормальний спосіб роботи з SQLModel.
    if since is not None:
        query = query.where(Checkin.day >= since)
    if until is not None:
        query = query.where(Checkin.day <= until)

    return list(session.exec(query.order_by(Checkin.day.desc())).all())


@app.post("/habits/{habit_id}/checkins", status_code=201)
def create_checkin(
    habit_id: int, data: CheckinCreate, user: CurrentUser, session: SessionDep
) -> Checkin:
    """Відмітити звичку як виконану. Без вказаного дня — за сьогодні."""
    habit = get_habit_or_404(habit_id, user, session)

    today = user_today(user)
    day = data.day or today
    # Бот і календар у вебі вже не пропонують майбутніх днів, але прямий
    # запит їх оминає — без цієї перевірки серію можна накрутити наперед.
    if day > today:
        raise HTTPException(
            status_code=422, detail="Не можна відмітити день, який ще не настав"
        )
    if habit.archived_at and day > habit.archived_at:
        raise HTTPException(status_code=422, detail="Спершу віднови звичку з архіву")

    if find_checkin(habit_id, day, session) is not None:
        # 409 Conflict — "запит правильний, але суперечить поточному стану".
        # Саме той код, який треба для повторного створення того самого.
        raise HTTPException(status_code=409, detail="Цей день уже відмічено")

    checkin = Checkin(habit_id=habit_id, day=day)
    session.add(checkin)

    try:
        # Умову перевіряє база: паралельна пізніша відмітка не повинна
        # пересунути вже збережений ранніший початок уперед.
        session.execute(
            update(Habit)
            .where(
                Habit.id == habit_id,
                or_(Habit.start_date.is_(None), Habit.start_date > day),
            )
            .values(start_date=day)
        )
        session.commit()
    except IntegrityError:
        # Перевірка вище (find_checkin) не рятує від гонки: два запити
        # можуть обидва пройти її до того, як хоч один зробив commit —
        # саме так стається, коли людина двічі швидко тицяє ту саму
        # кнопку в боті. Другий INSERT впирається в UNIQUE(habit_id, day)
        # і без цього блоку впав би 500-кою замість чесного 409.
        # Той самий UNIQUE, який README називає "останньою лінією
        # захисту", тут і спрацьовує — просто треба зловити його гідно.
        session.rollback()
        raise HTTPException(status_code=409, detail="Цей день уже відмічено") from None

    session.refresh(checkin)
    return checkin


@app.delete("/habits/{habit_id}/checkins/{day}", status_code=204)
def delete_checkin(
    habit_id: int, day: date, user: CurrentUser, session: SessionDep
) -> None:
    """Зняти відмітку за конкретний день (натиснув помилково)."""
    get_habit_or_404(habit_id, user, session)

    checkin = find_checkin(habit_id, day, session)
    if checkin is None:
        raise HTTPException(status_code=404, detail="Відмітку не знайдено")

    session.delete(checkin)
    session.commit()


# ---------- Статистика ----------


@app.get("/stats")
def all_stats(
    user: CurrentUser, session: SessionDep, include_archived: bool = False
) -> list[HabitStats]:
    """Статистика всіх звичок одразу — рівно два запити до бази.

    Наївний варіант виглядав би так: взяти звички, а далі в циклі
    для кожної спитати її відмітки. Це N+1 запитів — на сотні звичок
    база задихнеться. Тому беремо все одним запитом і групуємо в пам'яті.
    """
    query = select(Habit).where(Habit.user_id == user.id)
    if not include_archived:
        query = query.where(Habit.archived_at.is_(None))
    habits = session.exec(query).all()

    # Запит 2: відмітки всіх звичок ЦЬОГО користувача. Дістаємо лише
    # дві колонки, бо більше нічого й не потрібно.
    #
    # join(Habit) приєднує таблицю звичок, щоб дістатися до user_id:
    # у самої відмітки власника не записано, він відомий лише через звичку.
    # SQLAlchemy сам розуміє, як з'єднати таблиці — по foreign_key,
    # оголошеному в моделі Checkin. Запит при цьому лишається одним.
    rows = session.exec(
        select(Checkin.habit_id, Checkin.day)
        .join(Habit)
        .where(Habit.user_id == user.id)
    ).all()

    # Розкладаємо плаский список по звичках: {1: [дати], 2: [дати], ...}
    days_by_habit: dict[int, list[date]] = {}
    for habit_id, day in rows:
        days_by_habit.setdefault(habit_id, []).append(day)

    today = user_today(user)
    return [
        HabitStats(
            habit_id=habit.id,
            **stats.compute(
                days_by_habit.get(habit.id, []),
                today,
                weekdays=habit.weekdays,
                start_date=habit.start_date,
                archived_at=habit.archived_at,
            ),
        )
        for habit in habits
    ]


@app.get("/habits/{habit_id}/stats")
def habit_stats(habit_id: int, user: CurrentUser, session: SessionDep) -> HabitStats:
    """Порахувати показники звички: серії, всього днів, чи зроблено сьогодні."""
    habit = get_habit_or_404(habit_id, user, session)

    # Беремо з бази ЛИШЕ колонку day, а не цілі рядки.
    # Нам не потрібні id відміток — навіщо тягнути зайве.
    days = session.exec(select(Checkin.day).where(Checkin.habit_id == habit_id)).all()

    # Уся логіка — у stats.compute. Тут тільки дістали дані й віддали результат.
    return HabitStats(
        habit_id=habit_id,
        **stats.compute(
            list(days),
            user_today(user),
            weekdays=habit.weekdays,
            start_date=habit.start_date,
            archived_at=habit.archived_at,
        ),
    )


# ---------- Фронтенд ----------


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    """Відкрив корінь — потрапив на сторінку застосунку."""
    return RedirectResponse("/app/")


# Зібраний фронтенд. На відміну від старої теки static/, цих файлів
# немає в репозиторії: вони з'являються після `npm run build` у frontend/
# і навмисно занесені в .gitignore — зібраний код у git не місце,
# він відтворюється з вихідного однією командою.
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"

_MISSING_BUILD_PAGE = """
<!DOCTYPE html>
<html lang="uk"><head><meta charset="utf-8"><title>Фронтенд не зібрано</title></head>
<body style="font: 16px/1.6 system-ui; max-width: 40rem; margin: 4rem auto; padding: 0 1rem">
  <h1>Фронтенд не зібрано</h1>
  <p>API працює — подивитися його можна на <a href="/docs">/docs</a>.
     А от сторінки застосунку немає: теки <code>frontend/dist</code> не існує.</p>
  <p>Зібрати:</p>
  <pre style="background:#f4f4f5;padding:1rem;border-radius:8px">cd frontend
npm install
npm run build</pre>
</body></html>
"""

# mount підключає цілу теку як статичні файли: браузер зможе забрати
# /app/index.html і /app/assets/*.js.
# html=True означає "на /app/ віддавай index.html".
# Робимо це В КІНЦІ файлу навмисно: FastAPI перевіряє маршрути зверху вниз,
# і /habits має знайтися раніше, ніж справа дійде до статики.
if FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")
else:
    # StaticFiles на неіснуючій теці кидає виняток ПРИ СТАРТІ — застосунок
    # просто не піднявся б. Для людини, яка щойно склонувала репозиторій
    # і ще не бачила слова "npm", це виглядало б як зламаний проєкт.
    # Тому замість падіння — сторінка з інструкцією.
    @app.get("/app/{path:path}", include_in_schema=False)
    def frontend_not_built(path: str) -> HTMLResponse:
        return HTMLResponse(_MISSING_BUILD_PAGE, status_code=503)
