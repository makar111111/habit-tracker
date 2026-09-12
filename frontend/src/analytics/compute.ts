/**
 * Обчислення для екрана аналітики.
 *
 * Тут немає ні React, ні мережі — на вході масиви, на виході масиви.
 * Це та сама ідея, що й у `stats.py` на бекенді: складну частину
 * виносимо туди, де її можна перевірити тестом без браузера.
 */

import { addDays, toISO } from "../lib/dates";

/** Звичка разом із усіма її відмітками. */
export interface HabitDays {
  id: number;
  name: string;
  /** Дні у форматі `YYYY-MM-DD`. */
  days: Set<string>;
}

export interface DayPoint {
  /** `YYYY-MM-DD` — для підказки і для ключа. */
  date: string;
  /** Коротка підпис для осі: `12 вер`. */
  label: string;
  /** Скільки звичок відмічено того дня. */
  done: number;
  /** Скільки звичок узагалі існувало — для відсотка. */
  total: number;
}

export interface HabitPoint {
  name: string;
  /** Відсоток днів у періоді, коли звичку виконано. */
  percent: number;
  done: number;
}

export interface WeekdayPoint {
  label: string;
  percent: number;
  done: number;
}

const WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];

const MONTHS_SHORT = [
  "січ", "лют", "бер", "кві", "тра", "чер",
  "лип", "сер", "вер", "жов", "лис", "гру",
];

/**
 * Перелік останніх `days` днів, закінчуючи сьогоднішнім.
 *
 * Окрема функція, бо всі три графіки рахують «за період» і мають
 * дивитися на РІВНО той самий набір дат. Якби кожен будував свій
 * діапазон, розбіжність в один день ніхто б не помітив, а числа
 * перестали б сходитись між собою.
 */
export function lastDays(today: Date, days: number): Date[] {
  return Array.from({ length: days }, (_, i) => addDays(today, -(days - 1 - i)));
}

/** Скільки звичок виконано кожного дня періоду. */
export function dailySeries(habits: HabitDays[], today: Date, days: number): DayPoint[] {
  return lastDays(today, days).map((date) => {
    const iso = toISO(date);
    return {
      date: iso,
      label: `${date.getDate()} ${MONTHS_SHORT[date.getMonth()]}`,
      done: habits.filter((habit) => habit.days.has(iso)).length,
      total: habits.length,
    };
  });
}

/**
 * Дотримання кожної звички за період, у відсотках.
 *
 * Сортуємо за спаданням: у горизонтальній стовпчиковій діаграмі
 * впорядкованість — це не прикраса. Саме вона дозволяє прочитати
 * «хто попереду» одним рухом ока, без порівняння довжин навмання.
 */
export function habitSeries(habits: HabitDays[], today: Date, days: number): HabitPoint[] {
  const period = lastDays(today, days).map(toISO);

  return habits
    .map((habit) => {
      const done = period.filter((iso) => habit.days.has(iso)).length;
      return {
        name: habit.name,
        done,
        percent: Math.round((done / days) * 100),
      };
    })
    .sort((a, b) => b.percent - a.percent);
}

/**
 * Розподіл по днях тижня: коли виходить краще, а коли зривається.
 *
 * Рахуємо ВІДСОТОК, а не суму. Причина проста: за 30 днів понеділків
 * буває чотири або п'ять. На сумі зайвий понеділок виглядав би як
 * «понеділок — найпродуктивніший день», хоча це лише арифметика
 * календаря, а не поведінка людини.
 */
export function weekdaySeries(
  habits: HabitDays[],
  today: Date,
  days: number,
): WeekdayPoint[] {
  const done = Array(7).fill(0);
  const possible = Array(7).fill(0);

  for (const date of lastDays(today, days)) {
    // getDay(): 0 — неділя. Зсуваємо до європейського тижня з понеділка.
    const weekday = (date.getDay() + 6) % 7;
    const iso = toISO(date);

    possible[weekday] += habits.length;
    done[weekday] += habits.filter((habit) => habit.days.has(iso)).length;
  }

  return WEEKDAY_SHORT.map((label, index) => ({
    label,
    done: done[index],
    percent: possible[index] === 0 ? 0 : Math.round((done[index] / possible[index]) * 100),
  }));
}

/** Підсумкові числа для карток угорі екрана. */
export function summary(habits: HabitDays[], today: Date, days: number) {
  const period = lastDays(today, days).map(toISO);

  const totalCheckins = habits.reduce((sum, habit) => sum + habit.days.size, 0);

  const doneInPeriod = habits.reduce(
    (sum, habit) => sum + period.filter((iso) => habit.days.has(iso)).length,
    0,
  );

  const possible = habits.length * days;

  // Дні, коли зроблено хоч щось. Показник «я взагалі підходив до
  // трекера» — він набагато менш суворий, ніж відсоток виконання,
  // і в погані тижні саме він не дає опустити руки.
  const activeDays = period.filter((iso) =>
    habits.some((habit) => habit.days.has(iso)),
  ).length;

  return {
    totalCheckins,
    rate: possible === 0 ? 0 : Math.round((doneInPeriod / possible) * 100),
    activeDays,
    days,
  };
}
