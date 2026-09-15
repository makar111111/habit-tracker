"""Тести обмеження частоти запитів на POST /auth/login-code.

Дві частини: сам RateLimiter (з підробленим годинником, щоб перевіряти
«минула хвилина» без time.sleep) і ендпоїнт — що ліміт справді стоїть
перед записом у базу і що клієнт отримує 429 з Retry-After.
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

import main
from database import get_session
from main import app
from models import LoginToken
from rate_limit import RateLimiter


class FakeClock:
    """Годинник, який іде лише тоді, коли тест його переводить."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------- RateLimiter ----------


def test_allows_up_to_limit_then_blocks():
    limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())

    assert [limiter.hit("1.1.1.1") for _ in range(3)] == [None, None, None]
    assert limiter.hit("1.1.1.1") is not None


def test_retry_after_counts_from_oldest_attempt():
    """Вільне місце з'явиться, коли «випаде» НАЙСТАРІША спроба, а не остання."""
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    limiter.hit("ip")
    clock.now += 20
    limiter.hit("ip")
    clock.now += 10

    assert limiter.hit("ip") == pytest.approx(30)


def test_window_slides():
    """Ковзне вікно: через window_seconds після першої спроби місце звільняється."""
    clock = FakeClock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
    limiter.hit("ip")
    clock.now += 30
    limiter.hit("ip")

    clock.now += 29  # першій спробі 59 с — ще у вікні
    assert limiter.hit("ip") is not None

    clock.now += 1  # першій рівно 60 с — випала
    assert limiter.hit("ip") is None
    # А друга (їй 30 с) і щойно зарахована третя — вже знову повне вікно.
    assert limiter.hit("ip") is not None


def test_rejected_attempts_are_not_counted():
    """Хто стукає без упину, все одно дочекається вільного місця.

    Якби відмови теж записувались, кожна нова спроба відсувала б вікно,
    і наполегливий клієнт (або браузер із ретраями) не пройшов би ніколи.
    """
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    limiter.hit("ip")

    for _ in range(59):
        clock.now += 1
        assert limiter.hit("ip") is not None

    clock.now += 1
    assert limiter.hit("ip") is None


def test_keys_are_independent():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())

    assert limiter.hit("1.1.1.1") is None
    assert limiter.hit("1.1.1.1") is not None
    assert limiter.hit("2.2.2.2") is None


def test_sweep_forgets_idle_keys(monkeypatch: pytest.MonkeyPatch):
    """Адреси, які давно не приходили, не мають накопичуватись у пам'яті."""
    monkeypatch.setattr(RateLimiter, "_SWEEP_THRESHOLD", 3)
    clock = FakeClock()
    limiter = RateLimiter(limit=5, window_seconds=60, clock=clock)
    for ip in ("a", "b", "c", "d"):
        limiter.hit(ip)

    clock.now += 61
    limiter.hit("e")

    assert set(limiter._hits) == {"e"}


# ---------- ендпоінт ----------


@pytest.fixture(name="login_api")
def login_api_fixture(monkeypatch: pytest.MonkeyPatch):
    """Налаштований вхід, тимчасова база і маленький ліміт із ручним годинником."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    clock = FakeClock()

    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        monkeypatch.setattr("main.SESSION_SECRET", "test-session-secret")
        monkeypatch.setattr("main.BOT_USERNAME", "testbot")
        # Ендпоінт звертається до main.login_code_limiter під час запиту,
        # тож підміна модульної змінної діє без зміни коду застосунку.
        monkeypatch.setattr(
            main,
            "login_code_limiter",
            RateLimiter(limit=3, window_seconds=60, clock=clock),
        )

        yield session, clock

        app.dependency_overrides.clear()


def client_from(ip: str) -> TestClient:
    return TestClient(app, client=(ip, 50000))


def test_endpoint_returns_429_after_limit(login_api):
    client = client_from("10.0.0.1")

    statuses = [client.post("/auth/login-code").status_code for _ in range(4)]

    assert statuses == [201, 201, 201, 429]


def test_429_has_retry_after_and_readable_detail(login_api):
    _, clock = login_api
    client = client_from("10.0.0.1")
    for _ in range(3):
        client.post("/auth/login-code")
    clock.now += 15.5

    response = client.post("/auth/login-code")

    assert response.status_code == 429
    # 60 - 15.5 = 44.5 → округлюємо ВГОРУ, щоб клієнт не прийшов зарано.
    assert response.headers["Retry-After"] == "45"
    assert "Забагато спроб" in response.json()["detail"]


def test_rejected_request_does_not_write_to_database(login_api):
    """Головна мета ліміту — щоб флуд не наповнював таблицю кодів."""
    session, _ = login_api
    client = client_from("10.0.0.1")

    for _ in range(10):
        client.post("/auth/login-code")

    assert len(session.exec(select(LoginToken)).all()) == 3


def test_limit_is_per_ip(login_api):
    """Флуд з однієї адреси не має закривати вхід іншим людям."""
    attacker = client_from("10.0.0.1")
    for _ in range(4):
        attacker.post("/auth/login-code")

    assert client_from("10.0.0.2").post("/auth/login-code").status_code == 201


def test_endpoint_allows_again_after_window(login_api):
    _, clock = login_api
    client = client_from("10.0.0.1")
    for _ in range(3):
        client.post("/auth/login-code")
    assert client.post("/auth/login-code").status_code == 429

    clock.now += 60

    assert client.post("/auth/login-code").status_code == 201
