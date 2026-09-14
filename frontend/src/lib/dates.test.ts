import { describe, expect, it } from "vitest";

import { addDays, daysBetween, formatDay, fromISO, gridStart, pluralDays, toISO } from "./dates";

describe("toISO", () => {
  it("доповнює місяць і день нулем", () => {
    expect(toISO(new Date(2026, 0, 5))).toBe("2026-01-05");
  });

  it("бере МІСЦЕВУ дату, а не UTC", () => {
    // Пізній вечір за місцевим часом. toISOString() для київського
    // поясу віддав би вже наступну добу — і відмітка лягла б на завтра.
    const lateEvening = new Date(2026, 8, 12, 23, 45);
    expect(toISO(lateEvening)).toBe("2026-09-12");
  });
});

describe("fromISO", () => {
  it("розбирає рядок у місцеву дату", () => {
    const date = fromISO("2026-09-12");
    expect(date.getFullYear()).toBe(2026);
    expect(date.getMonth()).toBe(8); // вересень, бо місяці з нуля
    expect(date.getDate()).toBe(12);
  });

  it("переживає повний оберт: toISO(fromISO(x)) === x", () => {
    for (const iso of ["2026-01-01", "2026-02-28", "2026-12-31"]) {
      expect(toISO(fromISO(iso))).toBe(iso);
    }
  });
});

describe("addDays", () => {
  it("переходить через межу місяця", () => {
    expect(toISO(addDays(new Date(2026, 0, 31), 1))).toBe("2026-02-01");
  });

  it("переходить через межу року", () => {
    expect(toISO(addDays(new Date(2026, 11, 31), 1))).toBe("2027-01-01");
  });

  it("не змінює вихідну дату", () => {
    const original = new Date(2026, 8, 12);
    addDays(original, 10);
    expect(toISO(original)).toBe("2026-09-12");
  });
});

describe("gridStart", () => {
  it("завжди повертає понеділок", () => {
    // Перевіряємо на цілому тижні: яку б із семи дат не дали,
    // початок сітки має бути понеділком.
    for (let offset = 0; offset < 7; offset++) {
      const date = addDays(new Date(2026, 8, 7), offset);
      // getDay(): 1 — понеділок.
      expect(gridStart(date).getDay()).toBe(1);
    }
  });

  it("покриває рівно потрібну кількість тижнів", () => {
    const today = new Date(2026, 8, 12); // субота
    const start = gridStart(today, 12);
    const lastCell = addDays(start, 12 * 7 - 1);

    expect(daysBetween(start, lastCell)).toBe(83);
    // Остання клітинка — неділя поточного тижня, тобто завтра
    // відносно суботи.
    expect(toISO(lastCell)).toBe("2026-09-13");
  });
});

describe("pluralDays", () => {
  it("розрізняє три форми", () => {
    expect(pluralDays(1)).toBe("день");
    expect(pluralDays(3)).toBe("дні");
    expect(pluralDays(8)).toBe("днів");
  });

  it("знає виняток 11–14", () => {
    // Найпоширеніша помилка: 11 закінчується на 1, тож наївне правило
    // сказало б «одинадцять день».
    expect(pluralDays(11)).toBe("днів");
    expect(pluralDays(12)).toBe("днів");
    expect(pluralDays(14)).toBe("днів");
    expect(pluralDays(21)).toBe("день");
    expect(pluralDays(22)).toBe("дні");
  });

  it("правильно поводиться з нулем", () => {
    expect(pluralDays(0)).toBe("днів");
  });
});

describe("daysBetween", () => {
  it("рахує різницю в днях", () => {
    expect(daysBetween(new Date(2026, 8, 1), new Date(2026, 8, 11))).toBe(10);
  });

  it("віддає від'ємне число, якщо друга дата раніша", () => {
    expect(daysBetween(new Date(2026, 8, 11), new Date(2026, 8, 1))).toBe(-10);
  });

  it("ігнорує час доби", () => {
    const morning = new Date(2026, 8, 1, 8, 0);
    const night = new Date(2026, 8, 2, 23, 59);
    expect(daysBetween(morning, night)).toBe(1);
  });
});

describe("formatDay", () => {
  const today = new Date(2026, 8, 13);

  it("у поточному році показує лише день і місяць", () => {
    expect(formatDay("2026-04-16", today)).toBe("16 кві");
  });

  it("в іншому році додає рік", () => {
    expect(formatDay("2025-12-31", today)).toBe("31 гру 2025");
  });
});
