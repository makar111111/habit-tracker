import { useState } from "react";

import { useArchiveHabit, useDeleteHabit, useHabitPending, useRenameHabit, useToggleCheckin } from "../api/hooks";
import type { HabitWithStats } from "../api/types";
import { pluralDays, toISO } from "../lib/dates";
import { pluralCompletions, scheduleLabel } from "../lib/schedule";
import { HabitHistory } from "./HabitHistory";

interface Props { item: HabitWithStats; today: Date }

export function HabitRow({ item, today }: Props) {
  const { habit, stats } = item;
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(habit.name);
  const toggle = useToggleCheckin();
  const remove = useDeleteHabit();
  const rename = useRenameHabit();
  const archive = useArchiveHabit();
  const pending = useHabitPending(habit.id);
  const archived = Boolean(habit.archived_at);
  const day = toISO(today);
  const seriesUnit = habit.weekdays.length === 7 ? pluralDays(stats.current_streak) : pluralCompletions(stats.current_streak);

  function submitRename(event: React.FormEvent) {
    event.preventDefault();
    if (pending || !draft.trim()) return;
    if (draft.trim() === habit.name) { setRenaming(false); return; }
    rename.mutate({ habitId: habit.id, name: draft.trim() }, { onSuccess: () => setRenaming(false) });
  }

  return <li className="habit">
    <div className="habit-row">
      {!archived && <button type="button" className="check" aria-pressed={stats.done_today} disabled={pending}
        aria-label={`${habit.name}: ${stats.done_today ? "зроблено сьогодні" : "не зроблено сьогодні"}`}
        onClick={() => { if (!pending) toggle.mutate({ habitId: habit.id, done: stats.done_today, day, today: day }); }}>
        <span aria-hidden="true">{stats.done_today ? "✓" : ""}</span>
      </button>}
      <div className="habit-main">
        {renaming ? <form className="rename-form" onSubmit={submitRename}>
          <input value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={100} required autoFocus aria-label="Нова назва звички" />
          <button type="submit" className="ghost-button" disabled={pending || !draft.trim()}>Зберегти</button>
          <button type="button" className="ghost-button" disabled={pending} onClick={() => setRenaming(false)}>Скасувати</button>
        </form> : <div className="habit-name" title={habit.description || undefined}>{habit.name}</div>}
        <div className="habit-meta">
          <span title={`Серія: ${stats.current_streak} ${seriesUnit}`}>🔥 {stats.current_streak} {seriesUnit}</span>
          <span>усього {stats.total} {pluralDays(stats.total)}</span>
          {stats.longest_streak > stats.current_streak && <span>рекорд {stats.longest_streak}</span>}
        </div>
        <div className="habit-meta"><span>{scheduleLabel(habit)}</span>
          {habit.start_date && <span>від {habit.start_date}</span>}
          {habit.archived_at && <span>архівовано {habit.archived_at}</span>}
        </div>
      </div>
      <div className="habit-actions">
        <button type="button" className="ghost-button" onClick={() => setOpen((value) => !value)} aria-expanded={open}
          aria-label={open ? "Сховати календар" : "Показати календар"} title="Календар">
          <span aria-hidden="true">{open ? "▾" : "▸"}</span>
        </button>
        <button type="button" className="ghost-button" disabled={pending} title="Перейменувати"
          onClick={() => { setDraft(habit.name); setRenaming(true); }} aria-label={`Перейменувати «${habit.name}»`}>
          <span aria-hidden="true">✎</span>
        </button>
        <button type="button" className="ghost-button" disabled={pending} title={archived ? "Відновити" : "Архівувати"}
          onClick={() => archive.mutate({ habitId: habit.id, archived: !archived })}
          aria-label={`${archived ? "Відновити" : "Архівувати"} «${habit.name}»`}>
          {archived ? "Відновити" : "В архів"}
        </button>
      </div>
    </div>
    {open && <>
      {habit.description && <p className="habit-description">{habit.description}</p>}
      <HabitHistory habit={habit} today={today} pending={pending} />
      <div className="history-actions"><button type="button" className="ghost-button danger" disabled={pending}
        onClick={() => {
          if (window.confirm(`Видалити «${habit.name}» разом з усіма відмітками? Цю дію неможливо скасувати.`)) remove.mutate(habit.id);
        }} aria-label={`Видалити «${habit.name}» назавжди`}>Видалити назавжди</button></div>
    </>}
  </li>;
}
