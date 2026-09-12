import { useState } from "react";

import { useCheckins, useDeleteHabit, useRenameHabit, useToggleCheckin } from "../api/hooks";
import type { HabitWithStats } from "../api/types";
import { pluralDays } from "../lib/dates";
import { Calendar } from "./Calendar";

interface Props {
  item: HabitWithStats;
  today: Date;
}

/**
 * Одна звичка: кнопка відмітки, назва, показники й дії.
 *
 * Календар завантажується ЛІНИВО — запит за відмітками йде лише тоді,
 * коли картку розгорнули. Для десяти звичок це різниця між одним
 * запитом і одинадцятьма при кожному відкритті сторінки.
 */
export function HabitRow({ item, today }: Props) {
  const { habit, stats } = item;

  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(habit.name);

  const toggle = useToggleCheckin();
  const remove = useDeleteHabit();
  const rename = useRenameHabit();

  // enabled: open — хук викликається завжди (правила хуків це вимагають),
  // але сам запит не піде, поки картка згорнута.
  const checkins = useCheckins(habit.id, open);
  const doneDays = new Set((checkins.data ?? []).map((c) => c.day));

  function submitRename(event: React.FormEvent) {
    event.preventDefault();
    const name = draft.trim();
    if (name && name !== habit.name) {
      rename.mutate({ habitId: habit.id, name });
    }
    setRenaming(false);
  }

  return (
    <li className="habit">
      <div className="habit-row">
        <button
          type="button"
          className="check"
          // aria-pressed перетворює звичайну кнопку на перемикач:
          // диктор скаже "натиснуто" / "не натиснуто" замість того,
          // щоб мовчки прочитати емодзі.
          aria-pressed={stats.done_today}
          aria-label={`${habit.name}: ${stats.done_today ? "зроблено сьогодні" : "не зроблено сьогодні"}`}
          onClick={() => toggle.mutate({ habitId: habit.id, done: stats.done_today })}
        >
          <span aria-hidden="true">{stats.done_today ? "✓" : ""}</span>
        </button>

        <div className="habit-main">
          {renaming ? (
            <form onSubmit={submitRename}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={submitRename}
                maxLength={100}
                // autoFocus тут доречний: поле з'явилося саме тому, що
                // людина щойно натиснула «перейменувати».
                autoFocus
                aria-label="Нова назва звички"
              />
            </form>
          ) : (
            <div className="habit-name" title={habit.description || undefined}>
              {habit.name}
            </div>
          )}

          <div className="habit-meta">
            <span>🔥 {stats.current_streak}</span>
            <span>
              усього {stats.total} {pluralDays(stats.total)}
            </span>
            {stats.longest_streak > stats.current_streak && (
              <span>рекорд {stats.longest_streak}</span>
            )}
          </div>
        </div>

        <div className="habit-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={open ? "Сховати календар" : "Показати календар"}
          >
            <span aria-hidden="true">{open ? "▾" : "▸"}</span>
          </button>

          <button
            type="button"
            className="ghost-button"
            onClick={() => {
              setDraft(habit.name);
              setRenaming(true);
            }}
            aria-label={`Перейменувати «${habit.name}»`}
          >
            <span aria-hidden="true">✎</span>
          </button>

          <button
            type="button"
            className="ghost-button danger"
            onClick={() => {
              // Видалення звички забирає з собою всі її відмітки —
              // це втрата даних, тому питаємо. window.confirm грубий,
              // але чесний: з ним неможливо випадково погодитись.
              if (window.confirm(`Видалити «${habit.name}» разом з усіма відмітками?`)) {
                remove.mutate(habit.id);
              }
            }}
            aria-label={`Видалити «${habit.name}»`}
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      </div>

      {open && (
        checkins.isLoading ? (
          <div className="calendar">
            <p className="chart-note">Завантажую відмітки…</p>
          </div>
        ) : (
          <Calendar doneDays={doneDays} today={today} />
        )
      )}
    </li>
  );
}
