import { useHabitsWithStats } from "../api/hooks";
import { toISO } from "../lib/dates";
import { isScheduledOn } from "../lib/schedule";
import { AddHabitForm } from "./AddHabitForm";
import { HabitList } from "./HabitList";
import { ProgressPanel } from "./ProgressPanel";
import { QueryError } from "./QueryError";

export function TodayScreen({ today }: { today: Date }) {
  const query = useHabitsWithStats(true);
  const day = toISO(today);
  const planned = query.items.filter((item) => isScheduledOn(item.habit, day));
  const other = query.items.filter((item) => !isScheduledOn(item.habit, day));
  return <>
    {!query.error && !query.isLoading && <ProgressPanel items={planned} />}
    <AddHabitForm today={today} />
    {query.error ? <QueryError error={query.error} retry={query.refetch} /> : <>
      {query.items.length > 0 && <h2 className="section-title">За розкладом сьогодні</h2>}
      <HabitList items={planned} isLoading={query.isLoading} today={today}
        emptyMessage={query.items.length ? "На сьогодні немає запланованих звичок." : undefined} />
      {other.length > 0 && <>
        <h2 className="section-title">Інші активні звички</h2>
        <p className="chart-note">Можна виконати додатково. Ці відмітки збережуться в історії.</p>
        <HabitList items={other} isLoading={false} today={today} />
      </>}
    </>}
  </>;
}
