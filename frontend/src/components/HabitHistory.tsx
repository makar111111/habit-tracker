import { useState } from "react";

import { useCheckins, useToggleCheckin } from "../api/hooks";
import type { Habit } from "../api/types";
import { CALENDAR_WEEKS, addDays, gridStart, toISO } from "../lib/dates";
import { Calendar } from "./Calendar";
import { QueryError } from "./QueryError";

export function HabitHistory({ habit, today, pending }: { habit: Habit; today: Date; pending: boolean }) {
  const [page, setPage] = useState(0);
  // Старі сторінки закінчуються неділею, інакше крайній день не потрапить у запит.
  const end = page === 0 ? today : addDays(gridStart(today, 1), 6 - page * CALENDAR_WEEKS * 7);
  const query = useCheckins(habit.id, true, end);
  const toggle = useToggleCheckin();
  const doneDays = new Set((query.data ?? []).map((checkin) => checkin.day));

  return <div className="habit-history">
    <div className="calendar-navigation">
      <button type="button" className="ghost-button" onClick={() => setPage((value) => value + 1)} disabled={pending}
        aria-label="Попередні 12 тижнів">← Раніше</button>
      <span>{toISO(gridStart(end))} — {toISO(end)}</span>
      <button type="button" className="ghost-button" onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={pending || page === 0}
        aria-label="Наступні 12 тижнів">Пізніше →</button>
    </div>
    {query.isError ? <QueryError error={query.error} retry={query.refetch} busy={query.isFetching} />
      : query.isLoading ? <p className="chart-note" aria-busy="true">Завантажую відмітки…</p>
      : <Calendar doneDays={doneDays} today={today} end={end} habit={habit} disabled={pending}
        onToggle={(day, done) => { if (!pending) toggle.mutate({ habitId: habit.id, day, done, today: toISO(today) }); }} />}
  </div>;
}
