"""Тести ендпоінтів.

Кожен тест отримує чистий client із порожньою базою в пам'яті.
Порядок тестів не має значення, і вони не заважають один одному.
"""

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


# ---------- звички ----------


def test_list_is_empty_at_start(client: TestClient):
    """Свіжа база — порожній список."""
    response = client.get("/habits")

    assert response.status_code == 200
    assert response.json() == []


def test_create_habit(client: TestClient):
    """Створення повертає 201 і призначений базою id."""
    response = client.post("/habits", json={"name": "Пити воду", "description": "2 л"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Пити воду"
    assert body["description"] == "2 л"
    assert isinstance(body["id"], int)


def test_create_habit_rejects_empty_name(client: TestClient):
    """Порожня назва не проходить валідацію і НЕ потрапляє в базу."""
    response = client.post("/habits", json={"name": ""})

    assert response.status_code == 422
    # Головне не код відповіді, а те, що сміття не збереглося.
    assert client.get("/habits").json() == []


def test_get_unknown_habit_returns_404(client: TestClient):
    response = client.get("/habits/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Звичку не знайдено"


def test_patch_changes_only_given_fields(client: TestClient, habit_id: int):
    """Найважливіший тест: часткове оновлення не стирає інші поля.

    Саме тут працює exclude_unset=True. Якщо колись його приберуть,
    цей тест впаде — і помилка не доїде до користувача.
    """
    client.patch(f"/habits/{habit_id}", json={"description": "новий опис"})

    habit = client.get(f"/habits/{habit_id}").json()
    assert habit["description"] == "новий опис"
    assert habit["name"] == "Тестова звичка"  # назва вціліла


def test_delete_habit(client: TestClient, habit_id: int):
    response = client.delete(f"/habits/{habit_id}")

    assert response.status_code == 204
    assert client.get(f"/habits/{habit_id}").status_code == 404


# ---------- відмітки ----------


def test_checkin_without_body_uses_today(client: TestClient, habit_id: int):
    response = client.post(f"/habits/{habit_id}/checkins", json={})

    assert response.status_code == 201
    assert response.json()["day"] == TODAY.isoformat()


def test_checkin_twice_same_day_conflicts(client: TestClient, habit_id: int):
    """Другу відмітку за той самий день база не приймає."""
    client.post(f"/habits/{habit_id}/checkins", json={})
    response = client.post(f"/habits/{habit_id}/checkins", json={})

    assert response.status_code == 409
    assert len(client.get(f"/habits/{habit_id}/checkins").json()) == 1


def test_checkin_for_unknown_habit(client: TestClient):
    response = client.post("/habits/999/checkins", json={})

    assert response.status_code == 404


def test_checkin_rejects_impossible_date(client: TestClient, habit_id: int):
    response = client.post(f"/habits/{habit_id}/checkins", json={"day": "2026-02-31"})

    assert response.status_code == 422


def _freeze_utc(monkeypatch, moment: datetime) -> None:
    """Зупинити "зараз" для user_today: тест не залежить від годинника."""
    import calendar_rules

    monkeypatch.setattr(calendar_rules, "now_utc", lambda: moment)


def test_checkin_rejects_future_day(client: TestClient, habit_id: int, monkeypatch):
    """Прямий запит не повинен накручувати відмітки наперед."""
    _freeze_utc(monkeypatch, datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    client.patch("/users/me", json={"timezone": "Europe/Kyiv"})
    start_before = client.get(f"/habits/{habit_id}").json()["start_date"]

    for future in ["2026-09-16", "2030-01-01"]:
        response = client.post(f"/habits/{habit_id}/checkins", json={"day": future})
        assert response.status_code == 422
        assert response.json()["detail"] == "Не можна відмітити день, який ще не настав"

    # Головне — що відмітка не збереглася і не зсунула start_date.
    assert client.get(f"/habits/{habit_id}/checkins").json() == []
    assert client.get(f"/habits/{habit_id}").json()["start_date"] == start_before


def test_checkin_accepts_today_and_yesterday(
    client: TestClient, habit_id: int, monkeypatch
):
    _freeze_utc(monkeypatch, datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    client.patch("/users/me", json={"timezone": "Europe/Kyiv"})

    for day in ["2026-09-15", "2026-09-14"]:
        response = client.post(f"/habits/{habit_id}/checkins", json={"day": day})
        assert response.status_code == 201
        assert response.json()["day"] == day


def test_future_check_uses_owner_timezone(
    client: TestClient, habit_id: int, monkeypatch
):
    """22:30 UTC 14 вересня — у Києві (UTC+3) вже 01:30 15 вересня.

    Якби сервер рахував "сьогодні" за UTC чи date.today(), киянину
    відмовили б у відмітці за його власне сьогодні.
    """
    _freeze_utc(monkeypatch, datetime(2026, 9, 14, 22, 30, tzinfo=timezone.utc))
    url = f"/habits/{habit_id}/checkins"

    client.patch("/users/me", json={"timezone": "UTC"})
    assert client.post(url, json={"day": "2026-09-15"}).status_code == 422

    client.patch("/users/me", json={"timezone": "Europe/Kyiv"})
    response = client.post(url, json={"day": "2026-09-15"})
    assert response.status_code == 201
    assert client.post(url, json={"day": "2026-09-16"}).status_code == 422


def test_delete_checkin(client: TestClient, habit_id: int):
    client.post(f"/habits/{habit_id}/checkins", json={})

    response = client.delete(f"/habits/{habit_id}/checkins/{TODAY.isoformat()}")

    assert response.status_code == 204
    assert client.get(f"/habits/{habit_id}/checkins").json() == []


def test_delete_missing_checkin_returns_404(client: TestClient, habit_id: int):
    response = client.delete(f"/habits/{habit_id}/checkins/2020-01-01")

    assert response.status_code == 404


def test_checkins_sorted_newest_first(client: TestClient, habit_id: int):
    client.post(f"/habits/{habit_id}/checkins", json={"day": YESTERDAY.isoformat()})
    client.post(f"/habits/{habit_id}/checkins", json={"day": TODAY.isoformat()})

    days = [c["day"] for c in client.get(f"/habits/{habit_id}/checkins").json()]

    assert days == [TODAY.isoformat(), YESTERDAY.isoformat()]


def test_since_filters_out_older_days(client: TestClient, habit_id: int):
    """Параметр запиту since обрізає історію знизу."""
    old = TODAY - timedelta(days=30)
    client.post(f"/habits/{habit_id}/checkins", json={"day": old.isoformat()})
    client.post(f"/habits/{habit_id}/checkins", json={"day": TODAY.isoformat()})

    response = client.get(
        f"/habits/{habit_id}/checkins", params={"since": TODAY.isoformat()}
    )

    days = [c["day"] for c in response.json()]
    assert days == [TODAY.isoformat()]


def test_deleting_habit_removes_its_checkins(client: TestClient, habit_id: int):
    """Після видалення звички не лишається відміток-сиріт."""
    client.post(f"/habits/{habit_id}/checkins", json={})
    client.delete(f"/habits/{habit_id}")

    # Звички немає — відмітки теж мають бути прибрані.
    # Перевіряємо через новий запит: якби сироти лишились,
    # вони спливли б у звички з тим самим id у майбутньому.
    assert client.get(f"/habits/{habit_id}/checkins").status_code == 404


# ---------- статистика ----------


def test_stats_for_habit_without_checkins(client: TestClient, habit_id: int):
    body = client.get(f"/habits/{habit_id}/stats").json()

    assert body["total"] == 0
    assert body["current_streak"] == 0
    assert body["done_today"] is False


def test_stats_counts_streak(client: TestClient, habit_id: int):
    client.post(f"/habits/{habit_id}/checkins", json={"day": YESTERDAY.isoformat()})
    client.post(f"/habits/{habit_id}/checkins", json={"day": TODAY.isoformat()})

    body = client.get(f"/habits/{habit_id}/stats").json()

    assert body["total"] == 2
    assert body["current_streak"] == 2
    assert body["done_today"] is True
    assert body["last_day"] == TODAY.isoformat()


def test_stats_for_unknown_habit(client: TestClient):
    assert client.get("/habits/999/stats").status_code == 404


# ---------- статистика всіх звичок одразу ----------


def test_all_stats_empty(client: TestClient):
    assert client.get("/stats").json() == []


def test_all_stats_has_entry_per_habit(client: TestClient):
    client.post("/habits", json={"name": "перша"})
    client.post("/habits", json={"name": "друга"})

    body = client.get("/stats").json()

    assert len(body) == 2
    assert {row["habit_id"] for row in body} == {1, 2}


def test_all_stats_matches_single_endpoint(client: TestClient, habit_id: int):
    """Спільний ендпоінт має давати те саме, що й окремий."""
    client.post(f"/habits/{habit_id}/checkins", json={"day": YESTERDAY.isoformat()})
    client.post(f"/habits/{habit_id}/checkins", json={"day": TODAY.isoformat()})

    single = client.get(f"/habits/{habit_id}/stats").json()
    combined = next(
        row for row in client.get("/stats").json() if row["habit_id"] == habit_id
    )

    assert combined == single


def test_all_stats_separates_habits(client: TestClient):
    """Відмітки однієї звички не мають потрапляти в статистику іншої."""
    first = client.post("/habits", json={"name": "перша"}).json()["id"]
    second = client.post("/habits", json={"name": "друга"}).json()["id"]
    client.post(f"/habits/{first}/checkins", json={})

    body = {row["habit_id"]: row for row in client.get("/stats").json()}

    assert body[first]["total"] == 1
    assert body[second]["total"] == 0


def test_all_stats_does_not_scale_queries_with_habits(
    client: TestClient, session: Session
):
    """Скільки б не було звичок, запитів до бази лишається три.

    Це тест не на правильність, а на продуктивність: він зафіксує,
    якщо хтось колись перепише ендпоінт через цикл із запитом усередині.

    Чому саме три, а не два, як було раніше: один запит тепер витрачається
    на пошук користувача під час автентифікації. Він однаковий завжди —
    від кількості звичок не залежить, а саме це тест і стереже.
    """
    for number in range(5):
        habit = client.post("/habits", json={"name": f"звичка {number}"}).json()
        client.post(f"/habits/{habit['id']}/checkins", json={})

    executed: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        executed.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get("/stats")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    selects = [q for q in executed if q.strip().upper().startswith("SELECT")]
    assert len(selects) == 3, f"очікували 3 запити, а було {len(selects)}"
