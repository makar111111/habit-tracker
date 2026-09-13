import { useHabitsWithStats } from "../api/hooks";
import { HabitList } from "./HabitList";
import { QueryError } from "./QueryError";

export function ArchiveScreen({ today }: { today: Date }) {
  const query = useHabitsWithStats(true, true);
  if (query.error) return <QueryError error={query.error} retry={query.refetch} />;
  return <>
    <p className="chart-note">Архів зберігає історію й показники. Віднови звичку, щоб повернути її до активних.</p>
    <HabitList items={query.items.filter((item) => item.habit.archived_at)}
      today={today} isLoading={query.isLoading} emptyMessage="Архів порожній." />
  </>;
}
