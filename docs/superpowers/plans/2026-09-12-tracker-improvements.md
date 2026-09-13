# Tracker Improvements Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for bounded tasks and review. Independent file ownership permits the parallel dispatch workflow. User authorized autonomous implementation of the reviewed improvements.

**Goal:** Make the existing tracker reliable and add schedules, history editing, archive, personal reminders, and data recovery.

**Architecture:** Extend the shared API compatibly. Keep database migration/backup operations separate from request handling. Frontend and bot consume the documented contract.

**Tech Stack:** Python, FastAPI, SQLModel/SQLite, aiogram, React, TypeScript, TanStack Query, Vitest.

## 1. API and domain — root

Files: models.py, main.py, auth.py, config.py, database.py, stats.py, new calendar_rules.py, new test_improvements.py.

- [x] Add failing endpoint regressions and assert the persisted list remains valid:
  ```python
  response = client.patch(f'/habits/{habit_id}', json={'name': ''})
  assert response.status_code == 422
  assert client.get('/habits').status_code == 200
  ```
- [x] Add tests for schedule fields, archive/restore, owner-only export, partial settings, timezone dates, and scheduled streaks.
- [x] Implement the exact fields and routes in the approved design, preserving existing route defaults.
- [x] Run targeted pytest, then all Python tests after integration, with PYTHON_DOTENV_DISABLED=1, DATABASE_URL=sqlite:// and unique --basetemp.

## 2. Frontend — frontend agent

Files: frontend/src/**, frontend/package.json if needed. Own this subtree exclusively.

- [x] Write failing tests for failed checkin queries (error + retry, no fabricated statistics), first-day rates, planned weekdays, and archive cutoffs.
- [x] Extend types/client/hooks, implement schedule-aware calculations, interactive calendar, archive/restore and settings/export.
- [x] Include integration tests for new user actions and 401 handling; ensure mutations cannot overwrite another pending change.
- [x] Run `npm test`, `npm run build`; expected all tests and strict TypeScript pass.

## 3. Migration, backup, deployment — infrastructure agent

Files: migrations.py, backups.py, test_migrations.py, test_backups.py, docker-compose.yml, Dockerfile, .dockerignore, .github/workflows/tests.yml, README.md, .env.example.

- [x] Create an old-schema temporary SQLite fixture; run upgrade twice; assert user/habit/checkin preservation and only one schema upgrade.
- [x] Verify backup through sqlite backup API and restore into a new destination, never overwriting a source or existing destination.
- [x] Implement `migrations.upgrade(engine)`; root calls it from database initialization. Add daily snapshot helper/CLI and explicit restore CLI with temporary tests.
- [x] Pass login settings in Compose, use /health, and add a container runtime smoke check in CI. Update setup/operations documentation to match actual auth and new features.
- [x] Run focused pytest. Docker runtime may be unavailable locally; CI must contain reproducible checks.

## 4. Bot — bot agent

Files: bot/**, test_reminders.py, test_bot_handlers.py, test_bot_api.py. Own this set exclusively.

- [x] Test personalized timezone/hour, disabled reminders, delivery failure retry, successful-day persistence, and HTML escaping.
- [x] Implement reminder-target endpoints in HabitsAPI; poll every minute and acknowledge only successful delivery. Preserve pure next_run_at tests if still useful.
- [x] Use user's date for undo and scheduled habits for pending-reminder lists. Preserve bot commands and behavior for daily active habits; archived habits stay out of active lists.
- [x] Run the bot test files after the root API contract lands.

## 5. Integration and review — root

- [x] Review each contribution against the spec, then inspect code quality and edge cases.
- [x] Run all Python/frontend tests and production build; fix actual failures.
- [x] Test the compiled app through the browser with dependency-overridden temporary data: create, mark/backfill, archive/restore, settings, error/retry, logout.
- [x] Confirm migrations and backups touch only test files, check git diff, update this checklist and deliver a concise account of changes and verification.
