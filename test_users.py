"""Тести розділення користувачів.

Найважливіший файл у проєкті з погляду безпеки. Решта тестів перевіряє,
що застосунок робить те, що треба. Ці — що він НЕ робить того, чого не треба:
не показує чужі звички і не пускає без правильного секрету.
"""

from typing import Callable

import pytest
from fastapi.testclient import TestClient

import auth

# Два умовні номери в Telegram. Різні люди.
OLENA = 111
IHOR = 222

Headers = Callable[[int], dict[str, str]]


# ---------- доступ бота ----------


def test_bot_needs_secret(client: TestClient, bot_headers: Headers):
    """Сам по собі X-Telegram-Id нічого не вартий.

    Це найголовніша перевірка всієї схеми. Якби заголовка з секретом
    не існувало, будь-хто надіслав би чужий номер і прочитав чужі звички.
    """
    response = client.get("/habits", headers={"X-Telegram-Id": str(OLENA)})

    assert response.status_code == 401


def test_bot_rejects_wrong_secret(client: TestClient, bot_headers: Headers):
    # Значення латиницею: у заголовок кирилиця просто не влізе (див. нижче).
    headers = bot_headers(OLENA) | {"X-Bot-Secret": "wrong-secret"}

    response = client.get("/habits", headers=headers)

    assert response.status_code == 401


def test_bot_access_closed_when_secret_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Порожній BOT_SECRET у .env має закривати доступ, а не відкривати всім.

    Пастка, заради якої написано цей тест: порівняння порожнього рядка
    з порожнім дає True. Без окремої перевірки «секрет не налаштовано»
    забутий рядок у .env перетворився б на вільний вхід для будь-кого.
    """
    monkeypatch.setattr(auth, "BOT_SECRET", "")

    response = client.get(
        "/habits", headers={"X-Telegram-Id": str(OLENA), "X-Bot-Secret": ""}
    )

    assert response.status_code == 503


def test_non_numeric_telegram_id_is_rejected(client: TestClient):
    """Нечисловий id відсікає сам FastAPI — до нашого коду справа не доходить."""
    response = client.get("/habits", headers={"X-Telegram-Id": "not-a-number"})

    assert response.status_code == 401


def test_cyrillic_in_header_is_impossible(client: TestClient):
    """Кирилиця в заголовку ламається ще на боці клієнта.

    Тест виглядає дивно — він перевіряє не наш код, а поведінку HTTP.
    Але саме через це обмеження ім'я користувача передається тілом запиту,
    і без цього тесту причина такого рішення з часом забудеться.
    """
    with pytest.raises(UnicodeEncodeError):
        client.get("/habits", headers={"X-Bot-Secret": "таємниця"})


# ---------- хто я ----------


def test_browser_gets_local_user(client: TestClient):
    """Запит без заголовків — це вебінтерфейс, у нього окремий користувач."""
    body = client.get("/users/me").json()

    assert body["telegram_id"] is None
    assert body["name"] == auth.LOCAL_USER_NAME


def test_telegram_user_is_created_on_first_request(
    client: TestClient, bot_headers: Headers
):
    """Окремої реєстрації немає: перший же запит заводить користувача."""
    body = client.get("/users/me", headers=bot_headers(OLENA)).json()

    assert body["telegram_id"] == OLENA


def test_same_telegram_id_is_the_same_user(client: TestClient, bot_headers: Headers):
    """Другий запит НЕ створює другого користувача з тим самим номером."""
    first = client.get("/users/me", headers=bot_headers(OLENA)).json()
    second = client.get("/users/me", headers=bot_headers(OLENA)).json()

    assert first["id"] == second["id"]


def test_can_save_cyrillic_name(client: TestClient, bot_headers: Headers):
    """Ім'я їде тілом запиту, тому кирилиця з нею проходить нормально."""
    response = client.patch(
        "/users/me", json={"name": "Олена"}, headers=bot_headers(OLENA)
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Олена"
    assert client.get("/users/me", headers=bot_headers(OLENA)).json()["name"] == "Олена"


# ---------- розділення звичок ----------


def test_users_do_not_see_each_others_habits(
    client: TestClient, bot_headers: Headers
):
    """Найважливіший тест файлу: список звичок у кожного свій."""
    client.post("/habits", json={"name": "Йога"}, headers=bot_headers(OLENA))
    client.post("/habits", json={"name": "Біг"}, headers=bot_headers(IHOR))

    olena = client.get("/habits", headers=bot_headers(OLENA)).json()
    ihor = client.get("/habits", headers=bot_headers(IHOR)).json()

    assert [h["name"] for h in olena] == ["Йога"]
    assert [h["name"] for h in ihor] == ["Біг"]


def test_browser_does_not_see_telegram_habits(
    client: TestClient, bot_headers: Headers
):
    """Локальний користувач вебінтерфейсу — теж окрема людина."""
    client.post("/habits", json={"name": "Йога"}, headers=bot_headers(OLENA))

    assert client.get("/habits").json() == []


def test_reading_someone_elses_habit_gives_404(
    client: TestClient, bot_headers: Headers
):
    """Чужа звичка відповідає «не знайдено», а не «заборонено».

    404 навмисно: 403 підтвердив би, що звичка з таким id існує,
    і перебором можна було б порахувати чужі записи.
    """
    habit = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()

    response = client.get(f"/habits/{habit['id']}", headers=bot_headers(IHOR))

    assert response.status_code == 404


def test_cannot_change_someone_elses_habit(client: TestClient, bot_headers: Headers):
    habit = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()

    response = client.patch(
        f"/habits/{habit['id']}", json={"name": "Зламано"}, headers=bot_headers(IHOR)
    )

    assert response.status_code == 404
    # Головне не код відповіді, а те, що звичка вціліла.
    owner_view = client.get(f"/habits/{habit['id']}", headers=bot_headers(OLENA))
    assert owner_view.json()["name"] == "Йога"


def test_cannot_delete_someone_elses_habit(client: TestClient, bot_headers: Headers):
    habit = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()

    response = client.delete(f"/habits/{habit['id']}", headers=bot_headers(IHOR))

    assert response.status_code == 404
    owner_view = client.get(f"/habits/{habit['id']}", headers=bot_headers(OLENA))
    assert owner_view.status_code == 200


def test_cannot_checkin_someone_elses_habit(client: TestClient, bot_headers: Headers):
    """Відмітити чужу звичку теж не можна — інакше можна було б псувати
    сусідові статистику, навіть не бачачи його даних."""
    habit = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()

    response = client.post(
        f"/habits/{habit['id']}/checkins", json={}, headers=bot_headers(IHOR)
    )

    assert response.status_code == 404


def test_habit_response_hides_user_id(client: TestClient, bot_headers: Headers):
    """Назовні внутрішній номер власника не виходить."""
    body = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()

    assert "user_id" not in body


# ---------- розділення статистики ----------


def test_stats_shows_only_own_habits(client: TestClient, bot_headers: Headers):
    """Спільна статистика не має протікати між користувачами.

    Тут легко помилитись: звички відфільтрувати не забувають, а от
    другий запит — по відмітках — часто лишають без фільтра, бо у самої
    відмітки власника немає. Цей тест ловить саме таку недоробку.
    """
    olena_habit = client.post(
        "/habits", json={"name": "Йога"}, headers=bot_headers(OLENA)
    ).json()
    ihor_habit = client.post(
        "/habits", json={"name": "Біг"}, headers=bot_headers(IHOR)
    ).json()

    client.post(
        f"/habits/{olena_habit['id']}/checkins", json={}, headers=bot_headers(OLENA)
    )
    client.post(
        f"/habits/{ihor_habit['id']}/checkins", json={}, headers=bot_headers(IHOR)
    )

    stats = client.get("/stats", headers=bot_headers(IHOR)).json()

    assert len(stats) == 1
    assert stats[0]["habit_id"] == ihor_habit["id"]
    assert stats[0]["total"] == 1
