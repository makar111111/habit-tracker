import { fromISO } from "./dates";

export const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
export const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];

export interface Schedule {
  start_date?: string | null;
  weekdays?: readonly number[];
  archived_at?: string | null;
}

/** Розклад враховує дати, коли звичка вже існувала і ще не була архівована. */
export function isScheduledOn(habit: Schedule, day: string): boolean {
  if (habit.start_date && day < habit.start_date) return false;
  if (habit.archived_at && day > habit.archived_at) return false;
  return (habit.weekdays ?? EVERY_DAY).includes((fromISO(day).getDay() + 6) % 7);
}

export function scheduleLabel(habit: Schedule): string {
  const days = habit.weekdays ?? EVERY_DAY;
  return days.length === 7 ? "Щодня" : days.map((day) => WEEKDAYS[day]).join(", ");
}

export function pluralCompletions(count: number): string {
  const last = count % 10;
  const lastTwo = count % 100;
  if (lastTwo >= 11 && lastTwo <= 14) return "запланованих виконань";
  if (last === 1) return "заплановане виконання";
  if (last >= 2 && last <= 4) return "заплановані виконання";
  return "запланованих виконань";
}
