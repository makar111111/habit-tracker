"""Трекер звичок: ендпоінти API та віддача сторінки застосунку."""

import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import stats
from auth import (
    SESSION_COOKIE,
    CurrentUser,
    RequireBot,
    get_or_create_user,
    make_session,
)
from config import (
    BOT_USERNAME,
    LOGIN_TOKEN_TTL_SECONDS,
    SESSION_SECRET,
    SESSION_TTL_SECONDS,
)
from database import create_db_and_tables, get_session
from models import (
    Checkin,
    CheckinCreate,
    Habit,
    HabitCreate,
    HabitPublic,
    HabitStats,
    HabitUpdate,
    LoginToken,
    User,
    UserPublic,
    UserUpdate,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Код до yield виконується на старті сервера, після yield — на зупинці."""
    create_db_and_tables()
    yield


app = FastAPI(title="Трекер звичок", lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]


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


@app.post("/auth/login-code", status_code=201)
def create_login_code(session: SessionDep) -> dict:
    """Видати браузеру одноразовий код і посилання на бота."""
    if not SESSION_SECRET or not BOT_USERNAME:
        raise HTTPException(
            status_code=503,
            detail="Вхід не налаштовано: у .env потрібні SESSION_SECRET "
                   "і BOT_USERNAME",
        )

    # token_urlsafe(32) — 32 випадкові байти. Підібрати перебором
    # неможливо, а саме на це й покладається безпека всього потоку.
    token = secrets.token_urlsafe(32)
    session.add(LoginToken(token=token))
    session.commit()

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
    login = session.get(LoginToken, token)

    if login is None or _is_expired(login):
        raise HTTPException(status_code=404, detail="Код недійсний або протермінований")

    login.user_id = user.id
    session.add(login)
    session.commit()
    return {"status": "confirmed"}


@app.get("/auth/login-code/{token}")
def poll_login_code(token: str, response: Response, session: SessionDep) -> dict:
    """Браузер питає: код уже підтверджено? Якщо так — видати сесію.

    Тут код і згорає: після обміну запис видаляється, тож повторно
    тим самим кодом сесію не отримати. Це важливо саме тому, що код
    видно в адресному рядку Telegram і він може лишитись в історії.
    """
    login = session.get(LoginToken, token)

    if login is None:
        raise HTTPException(status_code=404, detail="Код недійсний")

    if _is_expired(login):
        session.delete(login)
        session.commit()
        raise HTTPException(status_code=410, detail="Код протермінований")

    if login.user_id is None:
        return {"status": "pending"}

    user_id = login.user_id
    session.delete(login)
    session.commit()

    response.set_cookie(
        SESSION_COOKIE,
        make_session(user_id),
        max_age=SESSION_TTL_SECONDS,
        # httponly — cookie недосяжна для JavaScript. Якщо на сторінку
        # колись просочиться чужий скрипт, він не зможе її вкрасти.
        httponly=True,
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
    user.name = data.name
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


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
def list_habits(user: CurrentUser, session: SessionDep) -> list[HabitPublic]:
    """Повернути звички поточного користувача."""
    return list(session.exec(select(Habit).where(Habit.user_id == user.id)).all())


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
    habit = Habit.model_validate(data, update={"user_id": user.id})
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
    for field, value in data.model_dump(exclude_unset=True).items():
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
    checkins = session.exec(
        select(Checkin).where(Checkin.habit_id == habit_id)
    ).all()
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
    get_habit_or_404(habit_id, user, session)

    day = data.day or date.today()

    if find_checkin(habit_id, day, session) is not None:
        # 409 Conflict — "запит правильний, але суперечить поточному стану".
        # Саме той код, який треба для повторного створення того самого.
        raise HTTPException(status_code=409, detail="Цей день уже відмічено")

    checkin = Checkin(habit_id=habit_id, day=day)
    session.add(checkin)

    try:
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
        raise HTTPException(status_code=409, detail="Цей день уже відмічено")

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
def all_stats(user: CurrentUser, session: SessionDep) -> list[HabitStats]:
    """Статистика всіх звичок одразу — рівно два запити до бази.

    Наївний варіант виглядав би так: взяти звички, а далі в циклі
    для кожної спитати її відмітки. Це N+1 запитів — на сотні звичок
    база задихнеться. Тому беремо все одним запитом і групуємо в пам'яті.
    """
    habits = session.exec(select(Habit).where(Habit.user_id == user.id)).all()

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

    today = date.today()
    return [
        HabitStats(
            habit_id=habit.id,
            **stats.compute(days_by_habit.get(habit.id, []), today),
        )
        for habit in habits
    ]


@app.get("/habits/{habit_id}/stats")
def habit_stats(habit_id: int, user: CurrentUser, session: SessionDep) -> HabitStats:
    """Порахувати показники звички: серії, всього днів, чи зроблено сьогодні."""
    get_habit_or_404(habit_id, user, session)

    # Беремо з бази ЛИШЕ колонку day, а не цілі рядки.
    # Нам не потрібні id відміток — навіщо тягнути зайве.
    days = session.exec(
        select(Checkin.day).where(Checkin.habit_id == habit_id)
    ).all()

    # Уся логіка — у stats.compute. Тут тільки дістали дані й віддали результат.
    return HabitStats(habit_id=habit_id, **stats.compute(list(days), date.today()))


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
