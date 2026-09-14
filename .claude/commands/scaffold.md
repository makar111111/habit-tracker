---
argument-hint: <type> <name>
description: Створити новий компонент, ендпоїнт тощо за шаблоном проєкту
---

Create a new $0 named $1, following the patterns already used in this project.

If the type or the name is missing, stop and ask for it — do not guess.
If the name contains spaces (e.g. "User Card"), convert it to this project's naming for that type (`UserCard`, `user_card`) and say which name you used.

Before writing, read one or two existing files of the same type and copy their structure, naming and comment style (comments in Ukrainian).

Where things go:
- `component` → `frontend/src/components/$1.tsx` + `$1.test.tsx` next to it (Vitest + Testing Library). Named export only, never `export default`.
- `hook` / `lib` → `frontend/src/lib/` (API hooks → `frontend/src/api/hooks.ts`), with a `*.test.ts` next to it.
- `endpoint` → a route in `main.py`, Pydantic schemas next to the existing ones, and tests in the matching `test_*.py` using the `client` fixture from `conftest.py`.
- `handler` (Telegram bot) → a module in `bot/`, talking to the API over HTTP (see README), with a test.
- Any other type: say what you would create and where, and wait for confirmation.

Project rules (from CLAUDE.md):
- New functionality ships with a test; the task is not done until the new tests and the linter (`ruff check .` / `npm run lint`) pass.
- Never touch `.env`, `habits.db` or `habits.db.*`; tests use a temporary database via `app.dependency_overrides[get_session]`.
- No secrets in code — settings come from `.env` through `config.py`.

Do not register the new thing in unrelated places (routes, menus, screens) unless asked. Report in Ukrainian: the files created and what is left to wire up.
