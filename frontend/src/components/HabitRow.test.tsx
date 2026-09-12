import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import { ApiError } from "../api/client";
import { createQueryClient } from "../api/queryClient";
import type { HabitWithStats } from "../api/types";
import { daysBetween, fromISO } from "../lib/dates";
import { resetErrorBus } from "../lib/errorBus";
import { ErrorBanner } from "./ErrorBanner";
import { HabitRow } from "./HabitRow";

const TODAY = new Date(2026, 8, 12);

const item: HabitWithStats = {
  habit: { id: 7, name: "Йога", description: "" },
  stats: {
    habit_id: 7,
    total: 88,
    current_streak: 3,
    longest_streak: 14,
    done_today: false,
    last_day: "2026-09-11",
  },
};

function renderRow() {
  return render(
    // Клієнт створюється тією ж функцією, що й у застосунку — разом
    // із обробником помилок мутацій. Саме він і перевіряється нижче.
    <QueryClientProvider client={createQueryClient()}>
      <ErrorBanner />
      <HabitRow item={item} today={TODAY} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetErrorBus();
  vi.restoreAllMocks();
  vi.spyOn(client, "listCheckins").mockResolvedValue([]);
});

describe("Календар просить лише потрібний період", () => {
  it("не ходить у мережу, поки картку не розгорнули", () => {
    renderRow();
    expect(client.listCheckins).not.toHaveBeenCalled();
  });

  it("передає межу періоду, а не тягне всю історію", async () => {
    // Спершу тут меж не було: `listCheckins(id)` просила ВСІ відмітки
    // звички. На місячній звичці різниці не видно, а через три роки це
    // понад тисяча записів, щоб намалювати 84 квадратики.
    renderRow();

    await userEvent.click(screen.getByLabelText("Показати календар"));

    await waitFor(() => expect(client.listCheckins).toHaveBeenCalled());

    const [habitId, range] = vi.mocked(client.listCheckins).mock.calls[0];
    expect(habitId).toBe(7);
    expect(range?.since).toBeDefined();

    // Перевіряємо не конкретне число, а домовленість: вікна має
    // вистачати і на теплову карту (84 дні), і на аналітику (90).
    const depth = daysBetween(fromISO(range!.since!), new Date());
    expect(depth).toBeGreaterThanOrEqual(90);
  });
});

describe("Помилка дії видима", () => {
  it("показує повідомлення, коли відмітка не збереглася", async () => {
    // Регресія, заради якої написано цей тест: у першій версії нового
    // фронтенду помилки мутацій не показувалися НІДЕ. Галочка
    // оптимістично вмикалась і мовчки відскакувала назад — людина
    // не мала жодного способу зрозуміти, що сталося.
    vi.spyOn(client, "checkIn").mockRejectedValue(
      new ApiError(500, "База даних недоступна"),
    );

    renderRow();

    await userEvent.click(screen.getByLabelText("Йога: не зроблено сьогодні"));

    expect(await screen.findByRole("alert")).toHaveTextContent("База даних недоступна");
  });

  it("повідомлення можна прибрати", async () => {
    vi.spyOn(client, "checkIn").mockRejectedValue(new ApiError(500, "Щось пішло не так"));

    renderRow();
    await userEvent.click(screen.getByLabelText("Йога: не зроблено сьогодні"));
    await screen.findByRole("alert");

    await userEvent.click(screen.getByLabelText("Сховати повідомлення"));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("мовчить, коли все добре", async () => {
    vi.spyOn(client, "checkIn").mockResolvedValue(true);

    renderRow();
    await userEvent.click(screen.getByLabelText("Йога: не зроблено сьогодні"));

    await waitFor(() => expect(client.checkIn).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
