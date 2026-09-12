import { describe, expect, it } from "vitest";

import { toISO } from "../lib/dates";
import { addDays } from "../lib/dates";
import {
  dailySeries,
  habitSeries,
  lastDays,
  summary,
  weekdaySeries,
  type HabitDays,
} from "./compute";

// Фіксована дата замість new Date(): тест, який залежить від
// сьогоднішнього дня, одного разу впаде сам по собі — найімовірніше
// в новорічну ніч, коли розбиратися найменше хочеться.
const TODAY = new Date(2026, 8, 12); // субота, 12 вересня 2026

function habit(name: string, days: string[]): HabitDays {
  return { id: name.length, name, days: new Set(days) };
}

/** Дні з відступом назад від TODAY — щоб не писати дати руками. */
function back(...offsets: number[]): string[] {
  return offsets.map((offset) => toISO(addDays(TODAY, -offset)));
}

describe("lastDays", () => {
  it("повертає потрібну кількість днів і закінчується сьогоднішнім", () => {
    const days = lastDays(TODAY, 30);
    expect(days).toHaveLength(30);
    expect(toISO(days[29])).toBe("2026-09-12");
    expect(toISO(days[0])).toBe("2026-08-14");
  });
});

describe("dailySeries", () => {
  it("рахує, скільки звичок відмічено кожного дня", () => {
    const habits = [habit("Йога", back(0, 1)), habit("Читання", back(0))];

    const series = dailySeries(habits, TODAY, 3);

    expect(series).toHaveLength(3);
    expect(series[2]).toMatchObject({ date: "2026-09-12", done: 2, total: 2 });
    expect(series[1]).toMatchObject({ date: "2026-09-11", done: 1 });
    expect(series[0]).toMatchObject({ date: "2026-09-10", done: 0 });
  });

  it("не падає без звичок", () => {
    expect(dailySeries([], TODAY, 3)).toHaveLength(3);
    expect(dailySeries([], TODAY, 3)[0].done).toBe(0);
  });
});

describe("habitSeries", () => {
  it("рахує відсоток від довжини періоду", () => {
    // 5 відміток за 10 днів = 50%.
    const habits = [habit("Йога", back(0, 1, 2, 3, 4))];
    expect(habitSeries(habits, TODAY, 10)[0].percent).toBe(50);
  });

  it("не зараховує відмітки поза періодом", () => {
    // Відмітка 40 днів тому не має впливати на десятиденне вікно.
    const habits = [habit("Йога", back(0, 40))];
    const result = habitSeries(habits, TODAY, 10)[0];
    expect(result.done).toBe(1);
    expect(result.percent).toBe(10);
  });

  it("сортує за спаданням", () => {
    const habits = [habit("Мало", back(0)), habit("Багато", back(0, 1, 2))];
    const names = habitSeries(habits, TODAY, 10).map((point) => point.name);
    expect(names).toEqual(["Багато", "Мало"]);
  });
});

describe("weekdaySeries", () => {
  it("починає тиждень з понеділка", () => {
    expect(weekdaySeries([], TODAY, 7).map((p) => p.label)).toEqual([
      "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд",
    ]);
  });

  it("кладе відмітку в правильний день тижня", () => {
    // 12 вересня 2026 — субота.
    const habits = [habit("Йога", back(0))];
    const saturday = weekdaySeries(habits, TODAY, 7).find((p) => p.label === "Сб");
    expect(saturday?.done).toBe(1);
    expect(saturday?.percent).toBe(100);
  });

  it("рахує відсоток від можливого, а не суму", () => {
    // За 14 днів кожен день тижня трапляється двічі. Одна звичка,
    // відмічена в одну з двох субот, дає 50%, а не 100% і не «1».
    const twoWeeksAgoSaturday = back(7);
    const habits = [habit("Йога", twoWeeksAgoSaturday)];

    const saturday = weekdaySeries(habits, TODAY, 14).find((p) => p.label === "Сб");
    expect(saturday?.percent).toBe(50);
  });
});

describe("summary", () => {
  it("рахує підсумки за період", () => {
    const habits = [habit("Йога", back(0, 1, 2)), habit("Читання", back(0))];

    const result = summary(habits, TODAY, 10);

    // 4 відмітки з можливих 20 (2 звички × 10 днів) = 20%.
    expect(result.rate).toBe(20);
    expect(result.totalCheckins).toBe(4);
    // Активні дні — коли зроблено бодай щось: сьогодні, вчора, позавчора.
    expect(result.activeDays).toBe(3);
  });

  it("не ділить на нуль без звичок", () => {
    expect(summary([], TODAY, 30).rate).toBe(0);
  });

  it("рахує totalCheckins за весь час, а rate — лише за період", () => {
    // Відмітка 100 днів тому входить у загальну суму, але не в відсоток.
    const habits = [habit("Йога", back(0, 100))];
    const result = summary(habits, TODAY, 10);

    expect(result.totalCheckins).toBe(2);
    expect(result.rate).toBe(10); // 1 із 10 днів
  });
});
