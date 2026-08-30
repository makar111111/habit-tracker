"""Трекер звичок — крок 3: звички та відмітки їх виконання."""

from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

import stats
from database import create_db_and_tables, get_session
from models import (
    Checkin,
    CheckinCreate,
    Habit,
    HabitCreate,
    HabitStats,
    HabitUpdate,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Код до yield виконується на старті сервера, після yield — на зупинці."""
    create_db_and_tables()
    yield


app = FastAPI(title="Трекер звичок", lifespan=lifespan)

SessionDep = Annotated[Session, Depends(get_session)]


def get_habit_or_404(habit_id: int, session: Session) -> Habit:
    """Знайти звичку або одразу відповісти 404.

    Ця перевірка потрібна майже в кожному ендпоінті, тож винесена окремо,
    щоб не копіювати ті самі три рядки шість разів.
    """
    habit = session.get(Habit, habit_id)
    if habit is None:
        raise HTTPException(status_code=404, detail="Звичку не знайдено")
    return habit


# ---------- Звички ----------


@app.get("/habits")
def list_habits(session: SessionDep) -> list[Habit]:
    """Повернути всі звички."""
    return list(session.exec(select(Habit)).all())


@app.get("/habits/{habit_id}")
def get_habit(habit_id: int, session: SessionDep) -> Habit:
    """Повернути одну звичку за id."""
    return get_habit_or_404(habit_id, session)


@app.post("/habits", status_code=201)
def create_habit(data: HabitCreate, session: SessionDep) -> Habit:
    """Створити нову звичку."""
    habit = Habit.model_validate(data)
    session.add(habit)
    session.commit()
    session.refresh(habit)
    return habit


@app.patch("/habits/{habit_id}")
def update_habit(habit_id: int, data: HabitUpdate, session: SessionDep) -> Habit:
    """Змінити назву та/або опис звички."""
    habit = get_habit_or_404(habit_id, session)

    # exclude_unset=True бере лише поля, які клієнт справді надіслав.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(habit, field, value)

    session.add(habit)
    session.commit()
    session.refresh(habit)
    return habit


@app.delete("/habits/{habit_id}", status_code=204)
def delete_habit(habit_id: int, session: SessionDep) -> None:
    """Видалити звичку разом з усіма її відмітками."""
    habit = get_habit_or_404(habit_id, session)

    # Спершу приберемо відмітки. Інакше в базі лишились би "сироти" —
    # відмітки, що посилаються на звичку, якої вже не існує.
    checkins = session.exec(select(Checkin).where(Checkin.habit_id == habit_id))
    for checkin in checkins:
        session.delete(checkin)

    session.delete(habit)
    session.commit()


# ---------- Відмітки ----------


@app.get("/habits/{habit_id}/checkins")
def list_checkins(
    habit_id: int,
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
    get_habit_or_404(habit_id, session)

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
    habit_id: int, data: CheckinCreate, session: SessionDep
) -> Checkin:
    """Відмітити звичку як виконану. Без вказаного дня — за сьогодні."""
    get_habit_or_404(habit_id, session)

    day = data.day or date.today()

    # Перевіряємо, чи такої відмітки ще немає.
    existing = session.exec(
        select(Checkin).where(Checkin.habit_id == habit_id, Checkin.day == day)
    ).first()
    if existing is not None:
        # 409 Conflict — "запит правильний, але суперечить поточному стану".
        # Саме той код, який треба для повторного створення того самого.
        raise HTTPException(status_code=409, detail="Цей день уже відмічено")

    checkin = Checkin(habit_id=habit_id, day=day)
    session.add(checkin)
    session.commit()
    session.refresh(checkin)
    return checkin


@app.delete("/habits/{habit_id}/checkins/{day}", status_code=204)
def delete_checkin(habit_id: int, day: date, session: SessionDep) -> None:
    """Зняти відмітку за конкретний день (натиснув помилково)."""
    get_habit_or_404(habit_id, session)

    checkin = session.exec(
        select(Checkin).where(Checkin.habit_id == habit_id, Checkin.day == day)
    ).first()
    if checkin is None:
        raise HTTPException(status_code=404, detail="Відмітку не знайдено")

    session.delete(checkin)
    session.commit()


# ---------- Статистика ----------


@app.get("/stats")
def all_stats(session: SessionDep) -> list[HabitStats]:
    """Статистика всіх звичок одразу — рівно два запити до бази.

    Наївний варіант виглядав би так: взяти звички, а далі в циклі
    для кожної спитати її відмітки. Це N+1 запитів — на сотні звичок
    база задихнеться. Тому беремо все одним запитом і групуємо в пам'яті.
    """
    habits = session.exec(select(Habit)).all()

    # Запит 2: усі відмітки всіх звичок. Дістаємо лише дві колонки,
    # бо більше нічого й не потрібно.
    rows = session.exec(select(Checkin.habit_id, Checkin.day)).all()

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
def habit_stats(habit_id: int, session: SessionDep) -> HabitStats:
    """Порахувати показники звички: серії, всього днів, чи зроблено сьогодні."""
    get_habit_or_404(habit_id, session)

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


# mount підключає цілу теку як статичні файли: браузер зможе забрати
# /app/index.html, /app/style.css і /app/app.js.
# html=True означає "на /app/ віддавай index.html".
# Робимо це В КІНЦІ файлу навмисно: FastAPI перевіряє маршрути зверху вниз,
# і /habits має знайтися раніше, ніж справа дійде до статики.
app.mount("/app", StaticFiles(directory="static", html=True), name="static")
