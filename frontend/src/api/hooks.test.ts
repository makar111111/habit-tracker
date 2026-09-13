import { createElement, type PropsWithChildren } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./client";
import { applyToggle, keys, useCheckins, useStats, useToggleCheckin, useUpdateMe } from "./hooks";
import { createQueryClient, endSession } from "./queryClient";
import type { Checkin, HabitStats, User } from "./types";

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

  it("чекає точного розрахунку серії сервером", () => {
    expect(after.current_streak).toBe(3);
  });

  it("не вигадує серію для відмітки поза розкладом", () => {
    const fresh = applyToggle(stats({ current_streak: 0, total: 0 }), false);
    expect(fresh.current_streak).toBe(0);
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

  it("зменшує загальну кількість, зберігаючи серію до відповіді сервера", () => {
    expect(after.total).toBe(10);
    expect(after.current_streak).toBe(4);
  });

  it("не йде нижче нуля", () => {
    // Захист від розсинхрону: якщо кеш чомусь відстав і показував нулі,
    // від'ємна серія виглядала б як поломка застосунку.
    const zeroed = applyToggle(stats({ done_today: true, total: 0, current_streak: 0 }), true);
    expect(zeroed.total).toBe(0);
    expect(zeroed.current_streak).toBe(0);
  });
});

afterEach(() => vi.restoreAllMocks());

describe("Паралельні відмітки", () => {
  it("оновлює підсумки після двох одночасних відміток з відкритими календарями", async () => {
    const client = createQueryClient();
    const date = new Date(2026, 8, 12);
    client.setQueryData(keys.stats, [stats(), stats({ habit_id: 2 })]);
    client.setQueryData(keys.checkins(1, "2026-05-16"), []);
    client.setQueryData(keys.checkins(2, "2026-05-16"), []);
    const finish: Array<(value: Checkin[]) => void> = [];
    vi.spyOn(api, "checkIn").mockResolvedValue(true);
    vi.spyOn(api, "listStats").mockResolvedValue([stats({ done_today: true }), stats({ habit_id: 2, done_today: true })]);
    vi.spyOn(api, "listCheckins").mockImplementation(() => new Promise((resolve) => { finish.push(resolve); }));
    const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => {
      useStats(true); useCheckins(1, true, date); useCheckins(2, true, date);
      return { first: useToggleCheckin(), second: useToggleCheckin() };
    }, { wrapper });
    act(() => {
      result.current.first.mutate({ habitId: 1, done: false, day: "2026-09-12", today: "2026-09-12" });
      result.current.second.mutate({ habitId: 2, done: false, day: "2026-09-12", today: "2026-09-12" });
    });
    await waitFor(() => expect(finish).toHaveLength(2));
    act(() => finish.forEach((resolve) => resolve([])));
    await waitFor(() => expect(result.current.first.isSuccess && result.current.second.isSuccess).toBe(true));
    await waitFor(() => expect(api.listStats).toHaveBeenCalledTimes(1));
    client.clear();
  });

  it("відкат помилки однієї звички не знімає іншу відмітку", async () => {
    const client = createQueryClient();
    client.setQueryData(keys.stats, [stats(), stats({ habit_id: 2 })]);
    let rejectFirst!: (reason: Error) => void;
    let resolveSecond!: (result: boolean) => void;
    vi.spyOn(api, "checkIn").mockImplementation((id) => id === 1
      ? new Promise<boolean>((_resolve, reject) => { rejectFirst = reject; })
      : new Promise<boolean>((resolve) => { resolveSecond = resolve; }));
    const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => ({ first: useToggleCheckin(), second: useToggleCheckin() }), { wrapper });
    act(() => {
      result.current.first.mutate({ habitId: 1, done: false, day: "2026-09-12", today: "2026-09-12" });
      result.current.second.mutate({ habitId: 2, done: false, day: "2026-09-12", today: "2026-09-12" });
    });
    await waitFor(() => expect(api.checkIn).toHaveBeenCalledTimes(2));
    act(() => rejectFirst(new Error("Помилка першої звички")));
    await waitFor(() => expect(result.current.first.isError).toBe(true));
    const cached = client.getQueryData<HabitStats[]>(keys.stats)!;
    expect(cached.find((row) => row.habit_id === 1)?.done_today).toBe(false);
    expect(cached.find((row) => row.habit_id === 2)?.done_today).toBe(true);
    act(() => resolveSecond(true));
    await waitFor(() => expect(result.current.second.isSuccess).toBe(true));
    client.clear();
  });

  it("передає саме дату користувача, а не браузера", async () => {
    const client = createQueryClient();
    vi.spyOn(api, "checkIn").mockResolvedValue(true);
    const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => useToggleCheckin(), { wrapper });
    act(() => result.current.mutate({ habitId: 1, done: false, day: "2025-02-12", today: "2025-02-12" }));
    await waitFor(() => expect(api.checkIn).toHaveBeenCalledWith(1, "2025-02-12"));
    client.clear();
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

describe("Завершення сеансу", () => {
  it("не дозволяє пізньому збереженню профілю повернути користувача після виходу", async () => {
    const client = createQueryClient();
    const current: User = { id: 1, telegram_id: 42, name: "Олена", timezone: "Europe/Kyiv", reminder_hour: 20, reminders_enabled: true };
    client.setQueryData(keys.me, current);
    let resolve!: (user: User) => void;
    vi.spyOn(api, "updateMe").mockImplementation(() => new Promise<User>((done) => { resolve = done; }));
    const wrapper = ({ children }: PropsWithChildren) => createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => useUpdateMe(), { wrapper });
    act(() => result.current.mutate({ timezone: "Asia/Tokyo" }));
    await waitFor(() => expect(api.updateMe).toHaveBeenCalledOnce());
    endSession(client);
    act(() => resolve({ ...current, timezone: "Asia/Tokyo" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(client.getQueryData(keys.me)).toBeNull();
    client.clear();
  });
});
