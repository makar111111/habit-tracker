"""Регресії й контракт покращень; лише тестові бази через get_session."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

import main
import stats
from models import CheckinCreate, Habit, User
from models import Checkin
from database import get_session


@pytest.mark.parametrize('changes', [
    {'name': ''}, {'name': '   '}, {'name': 'x' * 101}, {'name': None},
    {'description': None},
])
def test_invalid_patch_does_not_corrupt_list(client, habit_id, changes):
    unchecked = TestClient(main.app, raise_server_exceptions=False)
    response = unchecked.patch(f'/habits/{habit_id}', json=changes)
    assert response.status_code == 422
    assert client.get('/habits').status_code == 200
    assert client.get(f'/habits/{habit_id}').json()['name'] == 'Тестова звичка'


def test_trim_names_and_reject_blank_create(client):
    assert client.post('/habits', json={'name': '   '}).status_code == 422
    assert client.post('/habits', json={'name': '  Reading  '}).json()['name'] == 'Reading'


@pytest.mark.parametrize('path', ['/habits', '/telegram-users'])
def test_non_ascii_bot_secret_is_unauthorized(client, bot_headers, path):
    bot_headers(123)
    unchecked = TestClient(main.app, raise_server_exceptions=False)
    result = unchecked.get(path, headers=[(b'x-telegram-id', b'123'), (b'x-bot-secret', b'\xe9')])
    assert result.status_code == 401


@pytest.mark.parametrize('telegram_id', ['0', '-1', 'not-a-number', str(2**63), '9' * 5000],
                         ids=['zero', 'negative', 'non-numeric', 'overflow', 'very-long'])
def test_invalid_telegram_id_is_unauthorized_without_creating_user(client, bot_headers, session, telegram_id):
    headers = bot_headers(123) | {'X-Telegram-Id': telegram_id}
    unchecked = TestClient(main.app, raise_server_exceptions=False)
    assert unchecked.get('/users/me', headers=headers).status_code == 401
    assert session.exec(select(User)).all() == []


def test_schedule_and_start_date_persist(client):
    created = client.post('/habits', json={
        'name': 'Training', 'weekdays': [4, 0, 2], 'start_date': '2026-01-01',
    })
    assert created.status_code == 201
    habit = created.json()
    assert habit['weekdays'] == [0, 2, 4]
    assert habit['start_date'] == '2026-01-01'
    assert habit['archived_at'] is None
    assert client.get(f"/habits/{habit['id']}").json() == habit


@pytest.mark.parametrize('weekdays', [[], [-1], [7], [0, 0], [True], [1.5], None])
def test_invalid_weekday_schedule_is_rejected(client, weekdays):
    assert client.post('/habits', json={'name': 'Training', 'weekdays': weekdays}).status_code == 422


def test_archive_preserves_history_and_restore(client, habit_id):
    client.post(f'/habits/{habit_id}/checkins', json={})
    archived = client.patch(f'/habits/{habit_id}', json={'archived': True})
    assert archived.status_code == 200
    assert archived.json()['archived_at'] is not None
    assert client.get('/habits').json() == []
    assert client.get('/stats').json() == []
    assert len(client.get('/habits?include_archived=true').json()) == 1
    assert client.get('/stats?include_archived=true').json()[0]['total'] == 1
    assert len(client.get(f'/habits/{habit_id}/checkins').json()) == 1
    assert client.patch(f'/habits/{habit_id}', json={'archived': False}).json()['archived_at'] is None
    assert client.get('/stats').json()[0]['total'] == 1


def test_archive_is_owner_scoped(client, habit_id, bot_headers):
    assert client.patch(f'/habits/{habit_id}', json={'archived': True}, headers=bot_headers(111)).status_code == 404
    assert client.get('/habits').json()[0]['archived_at'] is None


def test_archive_rejects_later_checkins(client, habit_id):
    archived = client.patch(f'/habits/{habit_id}', json={'archived': True}).json()
    later = date.fromisoformat(archived['archived_at']) + timedelta(days=1)
    assert client.post(f'/habits/{habit_id}/checkins', json={'day': later.isoformat()}).status_code == 422


def test_backfill_moves_start_date_without_losing_history(client, habit_id):
    response = client.post(f'/habits/{habit_id}/checkins', json={'day': '2020-03-01'})
    assert response.status_code == 201
    assert client.get(f'/habits/{habit_id}').json()['start_date'] == '2020-03-01'


def test_backfill_keeps_earliest_start_when_another_request_read_old_state(client, habit_id, session):
    # Both requests loaded the old start before either one saved its backfill.
    with Session(session.get_bind()) as first, Session(session.get_bind()) as second:
        first_habit = first.get(Habit, habit_id)
        second_habit = second.get(Habit, habit_id)
        first_user = first.get(User, first_habit.user_id)
        second_user = second.get(User, second_habit.user_id)
        main.create_checkin(habit_id, CheckinCreate(day=date(2020, 3, 1)), first_user, first)
        main.create_checkin(habit_id, CheckinCreate(day=date(2020, 4, 1)), second_user, second)

    with Session(session.get_bind()) as fresh:
        assert fresh.get(Habit, habit_id).start_date == date(2020, 3, 1)


def test_user_preferences_are_partial_and_validated(client):
    client.patch('/users/me', json={'name': 'Olena'})
    result = client.patch('/users/me', json={
        'timezone': 'Asia/Tokyo', 'reminder_hour': 8, 'reminders_enabled': False,
    })
    assert result.status_code == 200
    assert result.json() == client.get('/users/me').json()
    assert result.json()['name'] == 'Olena'
    assert result.json()['timezone'] == 'Asia/Tokyo'
    assert result.json()['reminders_enabled'] is False
    for bad in [{'timezone': 'No/Such_Zone'}, {'reminder_hour': 24}, {'reminder_hour': -1}, {'name': None}, {'reminders_enabled': None}]:
        assert client.patch('/users/me', json=bad).status_code == 422


def test_user_local_day_controls_default_checkin(client, habit_id, monkeypatch):
    import calendar_rules
    monkeypatch.setattr(calendar_rules, 'now_utc', lambda: datetime(2026, 9, 12, 23, 30, tzinfo=timezone.utc))
    client.patch('/users/me', json={'timezone': 'Asia/Tokyo'})
    assert client.get('/users/me/today').json()['day'] == '2026-09-13'
    assert client.post(f'/habits/{habit_id}/checkins', json={}).json()['day'] == '2026-09-13'
    assert client.get(f'/habits/{habit_id}/stats').json()['done_today'] is True


def test_export_includes_archived_history_only_for_owner(client, habit_id, bot_headers):
    client.post(f'/habits/{habit_id}/checkins', json={'day': '2020-03-01'})
    client.patch(f'/habits/{habit_id}', json={'archived': True})
    foreign = client.post('/habits', json={'name': 'Private'}, headers=bot_headers(111)).json()
    exported = client.get('/users/me/export')
    assert exported.status_code == 200
    assert 'attachment' in exported.headers['content-disposition']
    data = exported.json()
    assert data['version'] == 1
    assert [h['id'] for h in data['habits']] == [habit_id]
    assert data['habits'][0]['archived_at'] is not None
    assert data['checkins'][0]['day'] == '2020-03-01'
    assert foreign['id'] not in [h['id'] for h in data['habits']]
    assert not any(key in data['user'] for key in ['last_reminder_day', 'token', 'secret'])


def test_export_is_a_consistent_snapshot_during_concurrent_creation(client, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export.sqlite3'}", connect_args={'check_same_thread': False})
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    SQLModel.metadata.create_all(engine)
    with Session(engine) as setup:
        user = User(name='Export owner')
        setup.add(user)
        setup.commit()
        setup.refresh(user)
        owner_id = user.id
        habit = Habit(user_id=owner_id, name='Original', start_date=date(2020, 1, 1))
        setup.add(habit)
        setup.commit()
        setup.refresh(habit)
        original_id = habit.id
        setup.add(Checkin(habit_id=original_id, day=date(2020, 1, 1)))
        setup.commit()

    class ExportRaceSession(Session):
        def exec(self, statement, **kwargs):
            result = super().exec(statement, **kwargs)
            if any(column.get('entity') is Habit for column in statement.column_descriptions):
                # Another request saves between the two export SELECTs.
                with Session(engine) as writer:
                    later = Habit(user_id=owner_id, name='Concurrent', start_date=date(2020, 1, 1))
                    writer.add(later)
                    writer.commit()
                    writer.refresh(later)
                    writer.add(Checkin(habit_id=later.id, day=date(2020, 1, 2)))
                    writer.commit()
            return result

    def override():
        with ExportRaceSession(engine) as session:
            yield session

    previous = main.app.dependency_overrides[get_session]
    main.app.dependency_overrides[get_session] = override
    try:
        response = client.get('/users/me/export')
        assert response.status_code == 200
        data = response.json()
        assert [habit['id'] for habit in data['habits']] == [original_id]
        assert [checkin['habit_id'] for checkin in data['checkins']] == [original_id]
    finally:
        main.app.dependency_overrides[get_session] = previous
        engine.dispose()


def test_reminder_targets_are_private_and_delivery_is_persisted(client, bot_headers):
    headers = bot_headers(111)
    client.get('/users/me', headers=headers)
    client.patch('/users/me', json={'reminders_enabled': False}, headers=bot_headers(222))
    assert client.get('/reminder-targets').status_code == 401
    targets = client.get('/reminder-targets', headers=headers).json()
    assert [target['telegram_id'] for target in targets] == [111]
    assert targets[0]['last_reminder_day'] is None
    assert client.post('/reminder-targets/111/sent', json={'day': '2026-09-12'}, headers=headers).status_code == 200
    assert client.get('/reminder-targets', headers=headers).json()[0]['last_reminder_day'] == '2026-09-12'
    client.post('/reminder-targets/111/sent', json={'day': '2026-09-11'}, headers=headers)
    assert client.get('/reminder-targets', headers=headers).json()[0]['last_reminder_day'] == '2026-09-12'


def test_scheduled_streak_survives_rest_days():
    days = [date(2026, 9, day) for day in (7, 9, 11)]
    result = stats.compute(days, date(2026, 9, 12), weekdays=[0, 2, 4])
    assert result['current_streak'] == 3
    assert result['longest_streak'] == 3
    assert stats.compute(days, date(2026, 9, 15), weekdays=[0, 2, 4])['current_streak'] == 0


def test_streak_ignores_days_outside_schedule_and_future():
    days = [date(2026, 9, day) for day in (7, 8, 9, 11, 14)]
    result = stats.compute(days, date(2026, 9, 12), weekdays=[0, 2, 4], start_date=date(2026, 9, 9))
    assert result['longest_streak'] == 2
    assert result['current_streak'] == 2
    assert result['total'] == 5  # raw history remains available


def test_health_does_not_create_a_user(client, session):
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    assert session.exec(select(User)).all() == []
