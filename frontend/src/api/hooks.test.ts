import { describe, expect, it } from "vitest";

import { applyToggle } from "./hooks";
import type { HabitStats } from "./types";

/**
 * Тести оптимістичної арифметики.
 *
 * Це найтонше місце нового фронтенду: кнопка перемикається ДО того,
 * як сервер відповість, а отже показники в цю мить рахує клієнт.
 * Помилка тут не впаде й не підсвітиться — вона просто покаже людині
 * неправильне число, яке за секунду мовчки виправиться. Таке ловиться
 * лише тестом.
 */

function stats(overrides: Partial<HabitStats> = {}): HabitStats {
  return {
    habit_id: 1,
    total: 10,
    current_streak: 3,
    longest_streak: 7,
    done_today: false,
    last_day: "2026-09-11",
    ...overrides,
  };
}

describe("applyToggle — ставимо відмітку", () => {
  const before = stats({ done_today: false, total: 10, current_streak: 3 });
  const after = applyToggle(before, false);

  it("позначає день зробленим", () => {
    expect(after.done_today).toBe(true);
  });

  it("збільшує загальну кількість на один", () => {
    expect(after.total).toBe(11);
  });

  it("подовжує серію на один", () => {
    expect(after.current_streak).toBe(4);
  });

  it("піднімає серію з нуля до одиниці", () => {
    const fresh = applyToggle(stats({ current_streak: 0, total: 0 }), false);
    expect(fresh.current_streak).toBe(1);
  });

  it("не змінює вихідний об'єкт", () => {
    // Мутація кешу на місці — класична причина того, що React
    // «не бачить» змін: посилання те саме, отже перемальовувати нічого.
    expect(before.done_today).toBe(false);
    expect(before.total).toBe(10);
  });
});

describe("applyToggle — знімаємо відмітку", () => {
  const after = applyToggle(stats({ done_today: true, total: 11, current_streak: 4 }), true);

  it("знімає позначку дня", () => {
    expect(after.done_today).toBe(false);
  });

  it("зменшує загальну кількість і серію на один", () => {
    expect(after.total).toBe(10);
    expect(after.current_streak).toBe(3);
  });

  it("не йде нижче нуля", () => {
    // Захист від розсинхрону: якщо кеш чомусь відстав і показував нулі,
    // від'ємна серія виглядала б як поломка застосунку.
    const zeroed = applyToggle(stats({ done_today: true, total: 0, current_streak: 0 }), true);
    expect(zeroed.total).toBe(0);
    expect(zeroed.current_streak).toBe(0);
  });
});

describe("applyToggle — межі відповідальності", () => {
  it("не чіпає longest_streak", () => {
    // Найдовшу серію за всю історію неможливо перерахувати, маючи лише
    // підсумки: потрібен повний список днів. Тому клієнт її не чіпає,
    // а правильне значення приносить наступна відповідь сервера.
    const after = applyToggle(stats({ longest_streak: 7 }), false);
    expect(after.longest_streak).toBe(7);
  });
});
