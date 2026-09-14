"""Одноразові коди не перевидаються при паралельному обміні."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, BrokenBarrierError

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

import auth
import main
from database import get_session
from models import LoginToken, User


@pytest.fixture
def login(client, monkeypatch):
    monkeypatch.setattr(auth, "ALLOW_LOCAL_USER", False)
    monkeypatch.setattr(auth, "BOT_SECRET", "review-test-bot")
    monkeypatch.setattr(auth, "SESSION_SECRET", "review-test-session")
    monkeypatch.setattr(main, "SESSION_SECRET", "review-test-session")
    monkeypatch.setattr(main, "BOT_USERNAME", "testbot")
    return client


def headers(user_id):
    return {"X-Bot-Secret": "review-test-bot", "X-Telegram-Id": str(user_id)}


def test_confirmation_cannot_change_the_owner(login):
    token = login.post("/auth/login-code").json()["token"]
    assert (
        login.post(
            "/auth/confirm", json={"token": token}, headers=headers(111)
        ).status_code
        == 200
    )
    assert (
        login.post(
            "/auth/confirm", json={"token": token}, headers=headers(111)
        ).status_code
        == 200
    )
    assert (
        login.post(
            "/auth/confirm", json={"token": token}, headers=headers(222)
        ).status_code
        == 409
    )
    login.get(f"/auth/login-code/{token}")
    assert login.get("/users/me").json()["telegram_id"] == 111


def test_issuing_code_removes_expired_rows(login, session):
    session.add(
        LoginToken(token="expired", created_at=datetime.now() - timedelta(days=1))
    )
    session.add(LoginToken(token="still-pending"))
    session.commit()
    assert login.post("/auth/login-code").status_code == 201
    assert session.get(LoginToken, "expired") is None
    assert session.get(LoginToken, "still-pending") is not None


def test_cookie_secure_is_configurable(login, monkeypatch):
    monkeypatch.setattr(main, "SESSION_COOKIE_SECURE", True)
    token = login.post("/auth/login-code").json()["token"]
    login.post("/auth/confirm", json={"token": token}, headers=headers(111))
    response = login.get(f"/auth/login-code/{token}")
    assert "; Secure" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


def test_parallel_exchange_issues_only_one_session(login, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'login.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        user = User(telegram_id=123)
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(LoginToken(token="race-code", user_id=user.id))
        session.commit()

    barrier = Barrier(2)

    class RacingSession(Session):
        def get(self, entity, ident, **kwargs):
            result = super().get(entity, ident, **kwargs)
            if entity is LoginToken:
                # Force the old read-then-delete implementation to read twice.
                try:
                    barrier.wait(timeout=0.3)
                except BrokenBarrierError:
                    pass
            return result

    def override():
        with RacingSession(engine) as session:
            yield session

    previous = main.app.dependency_overrides[get_session]
    main.app.dependency_overrides[get_session] = override
    try:

        def exchange(_):
            # No lifespan context: each request gets only the temporary DB.
            client = TestClient(main.app)
            try:
                return client.get("/auth/login-code/race-code")
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(exchange, [1, 2]))
        assert sorted(response.status_code for response in responses) == [200, 404]
        assert (
            sum(auth.SESSION_COOKIE in response.cookies for response in responses) == 1
        )
    finally:
        main.app.dependency_overrides[get_session] = previous
        engine.dispose()
