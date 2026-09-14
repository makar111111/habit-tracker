---
argument-hint: <file path>
description: Написати юніт-тести для вказаного файлу
---

Write unit tests for the file $ARGUMENTS. Cover the main cases and the obvious edge cases, and match the test style already used in this project.

If no file path was given, stop and ask for one — do not pick a file yourself.

Before writing:
- Read the file and find its existing tests (search for imports of it). Extend what exists instead of duplicating it; say which behaviour was already covered.
- Read one or two neighbouring test files to copy their style: test names and comments in Ukrainian, a comment explaining *why* for non-obvious cases.

Where tests go:
- Python (`*.py` in the project root) → `test_<module>.py` in the root, pytest; reuse fixtures from `conftest.py` (`client` etc.).
- Frontend (`frontend/src/**`) → a `*.test.ts` / `*.test.tsx` file next to the source, Vitest + Testing Library; named exports only.

Project rules (from CLAUDE.md):
- Never touch `habits.db`: anything using the database gets a temporary one via `app.dependency_overrides[get_session]`.
- Concurrency tests (`asyncio.gather`) need a session per request, not one shared session.
- Do not change the source file to make tests pass. If a test exposes a real bug, keep the test, stop, and report the bug.

Finish:
- Run only the new tests (`python -m pytest <file> -q` or `npm test -- --run <file>` in `frontend/`), then the linter (`ruff check` / `npm run lint`).
- Check that the key tests can fail: temporarily break the line they guard, rerun, then restore the source with `git checkout -- <file>`. A test that still passes is rewritten. (jsdom differs from a browser — e.g. it trims CSS variable values itself — so such a test can be green for the wrong reason.)
- Report in Ukrainian: the test file, a short list of covered cases, and the pass count.
