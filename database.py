"""Підключення до бази даних."""

from sqlmodel import Session, SQLModel, create_engine

# Три слеші — це "файл поруч зі мною". Тобто habits.db у папці проєкту.
DATABASE_URL = "sqlite:///habits.db"

# engine — це об'єкт, який уміє відкривати з'єднання з базою.
# Створюється один раз на весь застосунок.
# check_same_thread=False потрібен саме для SQLite: за замовчуванням він
# забороняє звертатись до себе з різних потоків, а FastAPI саме так і робить.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    """Створити файл бази й таблиці, якщо їх ще немає."""
    # SQLModel пам'ятає всі класи з table=True і створює таблиці під них.
    # Якщо таблиця вже є — нічого не робить, тож викликати безпечно.
    SQLModel.metadata.create_all(engine)


def get_session():
    """Видати сесію на час одного запиту й гарантовано закрити її після."""
    # Сесія — це "робочий сеанс" із базою: усередині нього ми читаємо й пишемо.
    # with гарантує, що сесія закриється навіть якщо всередині виникне помилка.
    with Session(engine) as session:
        yield session
