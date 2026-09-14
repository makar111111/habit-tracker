"""Тести входу у вебверсію через бота.

Найважливіший файл із погляду безпеки після test_users.py: тут
перевіряється не «чи працює», а «чи не пускає зайвого». Cookie сесії —
це ключ від чужих даних, і кожна перевірка нижче стереже одну зі
спроб його підробити або обійти.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import auth
import config
from auth import SESSION_COOKIE, make_session, read_session
from conftest import TEST_BOT_SECRET
from database import get_session
from main import app
from models import LoginToken, User

TEST_SESSION_SECRET = "test-session-secret"
OLENA = 111
IHOR = 222


@pytest.fixture(name="web")
def web_fixture(monkeypatch: pytest.MonkeyPatch):
    """Клієнт БЕЗ локального режиму — тобто такий, як бойовий сервер.

    Навмисно не використовує фікстуру `client` із conftest.py: та вмикає
    ALLOW_LOCAL_USER, і тоді перевіряти вхід було б безглуздо — застосунок
    пускав би й без нього.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session

        monkeypatch.setattr(auth, "ALLOW_LOCAL_USER", False)
        monkeypatch.setattr(auth, "BOT_SECRET", TEST_BOT_SECRET)
        monkeypatch.setattr(auth, "SESSION_SECRET", TEST_SESSION_SECRET)
        # main.py читає ці значення зі свого простору імен, тож
        # підміняти треба і там — інакше ендпоінт входу вважатиме,
        # що вхід не налаштовано.
        monkeypatch.setattr("main.SESSION_SECRET", TEST_SESSION_SECRET)
        monkeypatch.setattr("main.BOT_USERNAME", "testbot")

        yield TestClient(app), session

        app.dependency_overrides.clear()


def bot_headers(telegram_id: int) -> dict[str, str]:
    return {
        "X-Telegram-Id": str(telegram_id),
        "X-Bot-Secret": TEST_BOT_SECRET,
    }


# ---------- підпис cookie ----------


def test_session_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", TEST_SESSION_SECRET)

    assert read_session(make_session(42)) == 42


def test_tampered_user_id_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """Найочевидніша атака: підмінити свій id на чужий.

    user_id лежить у cookie відкритим текстом, тож підмінити його —
    справа однієї секунди. Захищає підпис: без SESSION_SECRET порахувати
    новий неможливо, а старий до змінених даних не підійде.
    """
    monkeypatch.setattr(auth, "SESSION_SECRET", TEST_SESSION_SECRET)
    cookie = make_session(42)

    _, expires, signature = cookie.split(".")
    forged = f"999.{expires}.{signature}"

    assert read_session(forged) is None


def test_expired_session_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """Протермінована cookie не діє, навіть із правильним підписом."""
    monkeypatch.setattr(auth, "SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setattr(auth, "SESSION_TTL_SECONDS", -10)  # уже в минулому

    assert read_session(make_session(42)) is None


def test_session_from_another_secret_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """Cookie, підписана іншим ключем, не приймається.

    Це і є спосіб відкликати всі сесії одразу: змінив SESSION_SECRET —
    усі видані раніше cookie стали недійсними.
    """
    monkeypatch.setattr(auth, "SESSION_SECRET", "секрет-номер-один")
    cookie = make_session(42)

    monkeypatch.setattr(auth, "SESSION_SECRET", "секрет-номер-два")
    assert read_session(cookie) is None


def test_garbage_cookie_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", TEST_SESSION_SECRET)

    for junk in ["", "abc", "1.2", "1.2.3.4", "не.число.підпис"]:
        assert read_session(junk) is None, junk


# ---------- доступ без входу ----------


def test_no_session_no_access(web):
    """Без cookie й без заголовків бота — 401, а не «локальний користувач».

    Саме заради цього рядка мінявся режим за замовчуванням: раніше
    такий запит мовчки отримував доступ до окремого акаунта, і на
    сервері це означало б відкриті двері.
    """
    client, _ = web

    assert client.get("/habits").status_code == 401


def test_bot_still_works_without_cookie(web):
    """Бот автентифікується заголовками — сесія йому не потрібна."""
    client, _ = web

    assert client.get("/habits", headers=bot_headers(OLENA)).status_code == 200


# ---------- потік входу ----------


def test_full_login_flow(web):
    """Наскрізний прохід: код -> підтвердження ботом -> сесія."""
    client, _ = web

    # 1. Браузер просить код.
    created = client.post("/auth/login-code")
    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["url"] == f"https://t.me/testbot?start={token}"

    # 2. Поки бот не підтвердив — очікування.
    assert client.get(f"/auth/login-code/{token}").json()["status"] == "pending"

    # 3. Людина створює звичку в боті, щоб потім було що побачити.
    client.post("/habits", json={"name": "Йога"}, headers=bot_headers(OLENA))

    # 4. Бот підтверджує код.
    confirmed = client.post(
        "/auth/confirm", json={"token": token}, headers=bot_headers(OLENA)
    )
    assert confirmed.status_code == 200

    # 5. Браузер обмінює код на сесію.
    exchanged = client.get(f"/auth/login-code/{token}")
    assert exchanged.json()["status"] == "confirmed"
    assert SESSION_COOKIE in exchanged.cookies

    # 6. І тепер бачить ТІ САМІ звички, що й бот.
    habits = client.get("/habits")
    assert habits.status_code == 200
    assert [h["name"] for h in habits.json()] == ["Йога"]


def test_login_code_is_single_use(web):
    """Код згорає після обміну.

    Код видно в адресному рядку Telegram і він лишається в історії чату,
    тож повторне використання має бути неможливим.
    """
    client, _ = web

    token = client.post("/auth/login-code").json()["token"]
    client.post("/auth/confirm", json={"token": token}, headers=bot_headers(OLENA))
    assert client.get(f"/auth/login-code/{token}").json()["status"] == "confirmed"

    assert client.get(f"/auth/login-code/{token}").status_code == 404


def test_expired_code_is_rejected(web):
    """Протермінований код не обміняти й не підтвердити."""
    client, session = web

    token = client.post("/auth/login-code").json()["token"]

    # Відсуваємо створення в минуле — швидше, ніж чекати п'ять хвилин.
    login = session.get(LoginToken, token)
    login.created_at = datetime.now() - timedelta(
        seconds=config.LOGIN_TOKEN_TTL_SECONDS + 60
    )
    session.add(login)
    session.commit()

    assert (
        client.post(
            "/auth/confirm", json={"token": token}, headers=bot_headers(OLENA)
        ).status_code
        == 404
    )
    assert client.get(f"/auth/login-code/{token}").status_code == 410


def test_unknown_code_is_rejected(web):
    client, _ = web

    assert client.get("/auth/login-code/вигаданий").status_code == 404
    assert (
        client.post(
            "/auth/confirm", json={"token": "вигаданий"}, headers=bot_headers(OLENA)
        ).status_code
        == 404
    )


def test_confirm_requires_bot_secret(web):
    """Підтвердити код без секрету бота неможливо.

    Інакше будь-хто, хто якось дізнався код, підтвердив би його сам
    і отримав сесію — уся схема тримається на тому, що підтверджує
    саме бот.
    """
    client, _ = web

    token = client.post("/auth/login-code").json()["token"]

    response = client.post(
        "/auth/confirm",
        json={"token": token},
        headers={"X-Telegram-Id": str(OLENA)},  # секрету немає
    )

    assert response.status_code == 401
    assert client.get(f"/auth/login-code/{token}").json()["status"] == "pending"


def test_session_belongs_to_confirming_user(web):
    """Сесію отримує той, ХТО ПІДТВЕРДИВ код, а не хто його створив.

    Це і є суть захисту від чужого посилання: якщо код підтвердить Ігор,
    сесія буде Ігоревою, і в браузері з'являться його звички, а не чужі.
    """
    client, _ = web

    client.post("/habits", json={"name": "Йога"}, headers=bot_headers(OLENA))
    client.post("/habits", json={"name": "Біг"}, headers=bot_headers(IHOR))

    token = client.post("/auth/login-code").json()["token"]
    client.post("/auth/confirm", json={"token": token}, headers=bot_headers(IHOR))
    client.get(f"/auth/login-code/{token}")

    assert [h["name"] for h in client.get("/habits").json()] == ["Біг"]


def test_logout_clears_access(web):
    client, _ = web

    token = client.post("/auth/login-code").json()["token"]
    client.post("/auth/confirm", json={"token": token}, headers=bot_headers(OLENA))
    client.get(f"/auth/login-code/{token}")
    assert client.get("/habits").status_code == 200

    client.post("/auth/logout")

    assert client.get("/habits").status_code == 401


def test_login_disabled_without_settings(web, monkeypatch: pytest.MonkeyPatch):
    """Без SESSION_SECRET вхід має чесно сказати «не налаштовано».

    Порожній секрет означає, що підпис нічого не вартий. Мовчки видавати
    такі сесії було б гірше, ніж не видавати зовсім.
    """
    client, _ = web
    monkeypatch.setattr("main.SESSION_SECRET", "")

    response = client.post("/auth/login-code")

    assert response.status_code == 503
    assert "SESSION_SECRET" in response.json()["detail"]


def test_session_of_deleted_user_gives_401(web):
    """Cookie з правильним підписом, але користувача вже немає."""
    client, session = web

    token = client.post("/auth/login-code").json()["token"]
    client.post("/auth/confirm", json={"token": token}, headers=bot_headers(OLENA))
    client.get(f"/auth/login-code/{token}")

    user = session.exec(
        __import__("sqlmodel").select(User).where(User.telegram_id == OLENA)
    ).one()
    session.delete(user)
    session.commit()

    assert client.get("/habits").status_code == 401
