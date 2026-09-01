"""Спільне налаштування для всіх тестів.

pytest знаходить цей файл автоматично — імпортувати його нікуди не треба.
Усе, що тут оголошено через @pytest.fixture, доступне будь-якому тесту:
достатньо назвати фікстуру в аргументах тестової функції.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import auth
from database import get_session
from main import app

# Секрет для тестів. Навмисно латиницею: HTTP-заголовки однобайтові,
# і кирилиця в них не проходить — та сама пастка, через яку ім'я
# користувача ми передаємо тілом запиту, а не заголовком.
TEST_BOT_SECRET = "test-secret-for-pytest"


@pytest.fixture(name="session")
def session_fixture():
    """Свіжа порожня база для одного тесту."""
    engine = create_engine(
        # "sqlite://" без шляху до файлу — база живе в оперативній пам'яті.
        # Твій habits.db при цьому не відкривається взагалі.
        "sqlite://",
        connect_args={"check_same_thread": False},
        # StaticPool змушує всі з'єднання користуватись ОДНИМ і тим самим
        # підключенням. Без цього кожне нове з'єднання отримало б свою
        # порожню базу в пам'яті — і тест бачив би не те, що записав.
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        yield session
    # Після yield тест завершився. Engine зникає разом з базою —
    # прибирати за собою не треба, наступний тест почне з чистого аркуша.


@pytest.fixture(name="client")
def client_fixture(session: Session):
    """Клієнт, який ходить у застосунок, але в тестову базу."""

    def get_session_override():
        return session

    # Ось найважливіший рядок. У main.py ендпоінти просять сесію
    # через Depends(get_session). Тут ми кажемо: "коли попросять
    # get_session — дай оце замість неї". Код застосунку не змінюється.
    app.dependency_overrides[get_session] = get_session_override

    # TestClient(app) БЕЗ конструкції with навмисно: так не запускається
    # lifespan, а отже не викликається create_db_and_tables(),
    # яка створила б справжній файл habits.db.
    yield TestClient(app)

    # Прибираємо підміну, щоб вона не протекла в інші тести.
    app.dependency_overrides.clear()


@pytest.fixture(name="bot_headers")
def bot_headers_fixture(monkeypatch: pytest.MonkeyPatch):
    """Заголовки, з якими бот звертається до API.

    Повертає не самі заголовки, а функцію: у тестах ізоляції потрібні
    кілька різних користувачів, тож id вказується на місці —
    bot_headers(111) і bot_headers(222) дають двох різних людей.

    monkeypatch підміняє справжній BOT_SECRET на тестовий і сам вертає
    все назад після тесту. Завдяки цьому тести не залежать від того,
    що саме лежить у твоєму .env, і працюють навіть без нього.
    """
    monkeypatch.setattr(auth, "BOT_SECRET", TEST_BOT_SECRET)

    def make(telegram_id: int) -> dict[str, str]:
        return {
            "X-Telegram-Id": str(telegram_id),
            "X-Bot-Secret": TEST_BOT_SECRET,
        }

    return make


@pytest.fixture(name="habit_id")
def habit_id_fixture(client: TestClient) -> int:
    """Готова звичка в базі. Повертає її id.

    Багато тестів починаються з "хай буде якась звичка" — щоб не
    повторювати ці три рядки всюди, виносимо їх сюди.
    """
    response = client.post("/habits", json={"name": "Тестова звичка"})
    return response.json()["id"]
