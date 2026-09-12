/**
 * Робота з датами. Ні React, ні мережі — самі функції над `Date`.
 *
 * Саме тому цей файл найлегше покрити тестами: передав дату, отримав
 * результат, порівняв. Складна логіка, витягнута туди, де немає
 * залежностей, — це загальний прийом, і в бекенді проєкту він теж
 * використаний (`stats.py` нічого не знає про базу даних).
 */

/** Скільки тижнів показує теплова карта. */
export const CALENDAR_WEEKS = 12;

export const MONTH_NAMES = [
  "січ", "лют", "бер", "кві", "тра", "чер",
  "лип", "сер", "вер", "жов", "лис", "гру",
];

/** Підписи рядків сітки. Порожні — щоб не рябіло: підписуємо через один. */
export const WEEKDAY_LABELS = ["Пн", "", "Ср", "", "Пт", "", "Нд"];

/**
 * Дата у форматі `YYYY-MM-DD` — саме такий чекає сервер.
 *
 * Чому не `toISOString()`: той переводить час у UTC. Для київського
 * вечора 12 вересня о 23:30 він поверне 13 вересня — і відмітка лягла б
 * на завтрашній день. Тут беремо місцеві getFullYear/getMonth/getDate,
 * тобто той самий день, який людина бачить у себе на годиннику.
 */
export function toISO(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Розібрати `YYYY-MM-DD` у місцеву дату (опівночі за місцевим часом). */
export function fromISO(iso: string): Date {
  const [year, month, day] = iso.split("-").map(Number);
  // Місяць у конструкторі Date рахується з нуля — класична пастка.
  return new Date(year, month - 1, day);
}

export function addDays(date: Date, days: number): Date {
  const copy = new Date(date);
  // setDate сам переносить через межу місяця й року:
  // 31 грудня + 1 день дає 1 січня наступного року, без арифметики вручну.
  copy.setDate(copy.getDate() + days);
  return copy;
}

/**
 * Перший день сітки календаря — понеділок, такий, щоб сітка закінчувалась
 * неділею поточного тижня і мала рівно CALENDAR_WEEKS стовпців.
 */
export function gridStart(today: Date, weeks: number = CALENDAR_WEEKS): Date {
  // getDay() віддає 0 для неділі, бо так вирішили в 1995 році.
  // Нам потрібен європейський тиждень, де понеділок — нульовий.
  const weekday = (today.getDay() + 6) % 7;
  const sunday = addDays(today, 6 - weekday);
  return addDays(sunday, -(weeks * 7 - 1));
}

/**
 * Українське відмінювання слова «день» за числом.
 *
 * Правило не «один/багато», як в англійській, а три форми з винятком
 * на 11–14: одинадцять ДНІВ, хоча один ДЕНЬ і двадцять один ДЕНЬ.
 */
export function pluralDays(n: number): string {
  const last = n % 10;
  const lastTwo = n % 100;
  if (lastTwo >= 11 && lastTwo <= 14) return "днів";
  if (last === 1) return "день";
  if (last >= 2 && last <= 4) return "дні";
  return "днів";
}

/** Скільки днів між двома датами (b - a). Час доби не враховується. */
export function daysBetween(a: Date, b: Date): number {
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  // Округлення обов'язкове: між двома опівночами може виявитись 23 або 25
  // годин, якщо в проміжок потрапив перехід на літній час.
  return Math.round((startOfDay(b).getTime() - startOfDay(a).getTime()) / MS_PER_DAY);
}

/** Людська дата на кшталт «12 вер». */
export function formatShort(date: Date): string {
  return `${date.getDate()} ${MONTH_NAMES[date.getMonth()]}`;
}
