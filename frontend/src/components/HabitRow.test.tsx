import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
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
  habit: {
    id: 7,
    name: "Йога",
    description: "",
    start_date: "2026-01-01",
    weekdays: [0, 1, 2, 3, 4, 5, 6],
    archived_at: null,
  },
  stats: {
    habit_id: 7,
    total: 88,
    current_streak: 3,
    longest_streak: 14,
    done_today: false,
    last_day: "2026-09-11",
  },
};

function renderRow(row: HabitWithStats = item) {
  return render(
    // Клієнт створюється тією ж функцією, що й у застосунку — разом
    // із обробником помилок мутацій. Саме він і перевіряється нижче.
    <QueryClientProvider client={createQueryClient()}>
      <ErrorBanner />
      <HabitRow item={row} today={TODAY} />
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

    await userEvent.click(screen.getByLabelText("Показати історію та дії"));

    await waitFor(() => expect(client.listCheckins).toHaveBeenCalled());

    const [habitId, range] = vi.mocked(client.listCheckins).mock.calls[0];
    expect(habitId).toBe(7);
    expect(range?.since).toBeDefined();

    // Перевіряємо не конкретне число, а домовленість: вікна має
    // вистачати і на теплову карту (84 дні), і на аналітику (90).
    const depth = daysBetween(fromISO(range!.since!), TODAY);
    expect(depth).toBeGreaterThanOrEqual(90);
  });
});

it("повторне натискання та календар блокуються до завершення запису", async () => {
  let finish!: (value: boolean) => void;
  vi.spyOn(client, "checkIn").mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  renderRow();
  await userEvent.click(screen.getByLabelText("Показати історію та дії"));
  await screen.findByRole("group", { name: "Відмітки за датами" });
  const button = screen.getByLabelText("Йога: не зроблено сьогодні");
  await userEvent.click(button);
  expect(button).toBeDisabled();
  expect(screen.getByRole("button", { name: /2026-09-11/ })).toBeDisabled();
  await userEvent.dblClick(button);
  expect(client.checkIn).toHaveBeenCalledTimes(1);
  act(() => finish(true));
  await waitFor(() => expect(button).toBeEnabled());
});

describe("Помилка дії видима", () => {
  it("показує повідомлення, коли відмітка не збереглася", async () => {
    // Регресія, заради якої написано цей тест: у першій версії нового
    // фронтенду помилки мутацій не показувалися НІДЕ. Галочка
    // оптимістично вмикалась і мовчки відскакувала назад — людина
    // не мала жодного способу зрозуміти, що сталося.
    vi.spyOn(client, "checkIn").mockRejectedValue(new ApiError(500, "База даних недоступна"));

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

describe("Картка на телефоні не розростається", () => {
  it("рідкісні дії сховані, доки картку не розгорнули", async () => {
    // Раніше «Перейменувати» й «В архів» стояли в рядку картки й на вузькому
    // екрані переносилися на окремий рядок — кожна картка ставала вдвічі вищою.
    renderRow();
    expect(screen.queryByRole("button", { name: "Перейменувати «Йога»" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Архівувати «Йога»" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Показати історію та дії"));

    expect(screen.getByRole("button", { name: "Перейменувати «Йога»" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Архівувати «Йога»" })).toBeInTheDocument();
  });

  it("в архіві «Відновити» лишається в рядку: там це головна дія", () => {
    renderRow({ ...item, habit: { ...item.habit, archived_at: "2026-09-10" } });
    expect(screen.getByRole("button", { name: "Відновити «Йога»" })).toBeInTheDocument();
  });

  it("не показує вогник, коли серії немає", () => {
    renderRow({ ...item, stats: { ...item.stats, current_streak: 0 } });
    expect(screen.queryByText(/🔥/)).not.toBeInTheDocument();
  });

  it("показує дати по-людськи, а не в ISO", () => {
    renderRow({ ...item, habit: { ...item.habit, start_date: "2025-11-03" } });
    expect(screen.getByText("від 3 лис 2025")).toBeInTheDocument();
    expect(screen.queryByText(/2025-11-03/)).not.toBeInTheDocument();
  });
});
