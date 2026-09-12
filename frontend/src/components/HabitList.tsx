import type { HabitWithStats } from "../api/types";
import { HabitRow } from "./HabitRow";

interface Props {
  items: HabitWithStats[];
  isLoading: boolean;
  today: Date;
}

export function HabitList({ items, isLoading, today }: Props) {
  if (isLoading) {
    // Три скелети — приблизно стільки звичок у типового користувача.
    // Сенс не в точності, а в тому, щоб сторінка не стрибала, коли
    // порожнє місце раптом заповниться вмістом.
    return (
      <ul className="habits">
        {[0, 1, 2].map((i) => (
          <li key={i} className="skeleton" />
        ))}
      </ul>
    );
  }

  if (items.length === 0) {
    return <p className="empty">Поки що жодної звички. Додай першу вище.</p>;
  }

  return (
    <ul className="habits">
      {items.map((item) => (
        // key — це не формальність: за ним React вирішує, який рядок
        // лишити на місці, а який перебудувати. Індекс масиву тут був
        // би помилкою: після видалення другої звички всі наступні
        // змістились би, і React вирішив би, що змінилися ВСІ рядки.
        <HabitRow key={item.habit.id} item={item} today={today} />
      ))}
    </ul>
  );
}
