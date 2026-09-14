Find every TODO and FIXME comment in the codebase and list them grouped by file.

Scope:
- Search only files tracked by git: use `git grep -n -I -E "\b(TODO|FIXME)\b" -- ":!.claude/commands/"`.
  This skips node_modules, frontend/dist, .venv and other build output, and excludes this command file (it mentions the words itself).
- Never read or search `habits.db`, `habits.db.*` or `.env`.

Output (in Ukrainian):
- Group by file; sort files by path, and each file's items by line number.
- One line per item: `рядок N — TODO|FIXME: текст коментаря` (link as `path:N`).
- Put FIXME before TODO in the summary: FIXME means something is broken.
- End with a summary line: total count, TODO vs FIXME, number of files.
- If nothing is found, say so in one sentence — do not invent items or list matches from ignored directories.
