import { cloneElement, type ReactElement } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { createQueryClient } from "./api/queryClient";
import { resetErrorBus } from "./lib/errorBus";

vi.mock("recharts", async (original) => ({
  ...await original<typeof import("recharts")>(),
  ResponsiveContainer: ({ children }: { children: ReactElement }) =>
    cloneElement(children, { width: 640, height: 220 } as object),
}));

const makeHabit = (id: number, name: string, weekdays = [0, 1, 2, 3, 4, 5, 6]) => ({
  id, name, description: "", start_date: "2026-09-12", weekdays, archived_at: null as string | null,
});
let habits: ReturnType<typeof makeHabit>[];
let user: { id: number; telegram_id: number; name: string; timezone: string; reminder_hour: number; reminders_enabled: boolean };
let checkins: { id: number; habit_id: number; day: string }[];
let signedIn: boolean;
let today: string;
let failPath: string | null;
let failureStatus: number;
let calls: { path: string; method: string; body: Record<string, unknown> }[];
const clients: ReturnType<typeof createQueryClient>[] = [];

beforeEach(() => {
  resetErrorBus();
  habits = [makeHabit(1, "Йога")];
  user = { id: 1, telegram_id: 42, name: "Олена", timezone: "Europe/Kyiv", reminder_hour: 21, reminders_enabled: true };
  checkins = [];
  signedIn = true;
  today = "2026-09-12";
  failPath = null;
  failureStatus = 500;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, options: RequestInit = {}) => {
    const url = new URL(input, "http://localhost");
    const path = url.pathname;
    const method = options.method ?? "GET";
    const body = options.body ? JSON.parse(String(options.body)) : {};
    calls.push({ path: input, method, body });
    const respond = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
    if (path === failPath) return respond({ detail: "Тимчасова помилка сервера" }, failureStatus);
    if (path === "/auth/login-code") return respond({ token: "login-token", url: "https://t.me/example?start=login-token", expires_in: 60 });
    if (path === "/auth/login-code/login-token") { signedIn = true; return respond({ status: "confirmed" }); }
    if (!signedIn) return respond({ detail: "Потрібен вхід" }, 401);
    if (path === "/users/me") {
      if (method === "PATCH") Object.assign(user, body);
      return respond(user);
    }
    if (path === "/users/me/today") return respond({ day: today });
    if (path === "/auth/logout") { signedIn = false; return new Response(null, { status: 204 }); }
    if (path === "/habits") {
      if (method === "POST") {
        const created = { ...makeHabit(10, String(body.name)), ...body };
        habits.push(created);
        return respond(created, 201);
      }
      return respond(habits.filter((habit) => url.searchParams.has("include_archived") || !habit.archived_at));
    }
    if (path === "/stats") return respond(habits.filter((habit) => url.searchParams.has("include_archived") || !habit.archived_at).map((habit) => ({
      habit_id: habit.id, total: habit.archived_at ? 200 : checkins.filter((c) => c.habit_id === habit.id).length,
      current_streak: 0, longest_streak: 4, done_today: checkins.some((c) => c.habit_id === habit.id && c.day === today), last_day: null,
    })));
    const habitPath = path.match(/^\/habits\/(\d+)$/);
    if (habitPath && method === "PATCH") {
      const habit = habits.find((h) => h.id === Number(habitPath[1]))!;
      if ("archived" in body) habit.archived_at = body.archived ? today : null;
      if (body.name) habit.name = String(body.name);
      return respond(habit);
    }
    const historyPath = path.match(/^\/habits\/(\d+)\/checkins(?:\/(\d{4}-\d{2}-\d{2}))?$/);
    if (historyPath) {
      const id = Number(historyPath[1]);
      if (method === "POST") {
        const created = { id: checkins.length + 1, habit_id: id, day: String(body.day) };
        checkins.push(created);
        const habit = habits.find((h) => h.id === id)!;
        if (created.day < habit.start_date) habit.start_date = created.day;
        return respond(created, 201);
      }
      if (method === "DELETE") {
        checkins = checkins.filter((c) => !(c.habit_id === id && c.day === historyPath[2]));
        return new Response(null, { status: 204 });
      }
      return respond(checkins.filter((c) => c.habit_id === id && c.day >= (url.searchParams.get("since") ?? "") && c.day <= (url.searchParams.get("until") ?? "9999")));
    }
    throw new Error(`Unexpected request: ${method} ${input}`);
  }));
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function openApp() {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, retryDelay: 0 } });
  clients.push(client);
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
  return client;
}

describe("Сеанс і відновлення після помилок", () => {
  it("показує вхід після 401 іншого запиту без повторних спроб", async () => {
    failPath = "/habits";
    failureStatus = 401;
    openApp();
    expect(await screen.findByRole("button", { name: "Увійти через Telegram" })).toBeInTheDocument();
    expect(calls.filter((call) => call.path === "/habits")).toHaveLength(1);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("дає повторити невдале завантаження профілю", async () => {
    failPath = "/users/me";
    openApp();
    expect(await screen.findByRole("alert")).toHaveTextContent("Тимчасова помилка");
    failPath = null;
    await userEvent.click(screen.getByRole("button", { name: "Спробувати ще раз" }));
    expect(await screen.findByText("Олена")).toBeInTheDocument();
  });

  it("вихід очищає приватні дані та повертає форму входу", async () => {
    const client = openApp();
    await screen.findByText("Йога");
    await userEvent.click(screen.getByRole("button", { name: "вийти" }));
    expect(await screen.findByRole("button", { name: "Увійти через Telegram" })).toBeInTheDocument();
    expect(screen.queryByText("Йога")).not.toBeInTheDocument();
    expect(client.getQueryData(["habits"])).toBeUndefined();
  });

  it("запізніле збереження профілю після виходу не відновлює завершений сеанс", async () => {
    const client = openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Налаштування" }));
    const immediateFetch = fetch;
    let finishSave!: () => void;
    vi.stubGlobal("fetch", vi.fn(async (...args: Parameters<typeof fetch>) => {
      const response = await immediateFetch(...args);
      if (String(args[0]) === "/users/me" && args[1]?.method === "PATCH") {
        return new Promise<Response>((resolve) => { finishSave = () => resolve(response); });
      }
      return response;
    }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Ім’я" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Ім’я" }), "Запізніла відповідь");
    await userEvent.click(screen.getByRole("button", { name: "Зберегти налаштування" }));
    await waitFor(() => expect(finishSave).toBeTypeOf("function"));
    await userEvent.click(screen.getByRole("button", { name: "вийти" }));
    await screen.findByRole("button", { name: "Увійти через Telegram" });
    const laterProfiles: unknown[] = [];
    const unsubscribe = client.getQueryCache().subscribe((event) => {
      if (event.type === "updated" && event.query.queryKey[0] === "me") laterProfiles.push(event.query.state.data);
    });
    await act(async () => { finishSave(); });
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(laterProfiles.every((profile) => profile === null)).toBe(true);
    expect(client.getQueryData(["me"])).toBeNull();
    expect(screen.getByRole("button", { name: "Увійти через Telegram" })).toBeInTheDocument();
    unsubscribe();
  });

  it("запізніле збереження старого сеансу не переписує профіль після нового входу", async () => {
    const client = openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Налаштування" }));
    const immediateFetch = fetch;
    let finishSave!: () => void;
    vi.stubGlobal("fetch", vi.fn(async (...args: Parameters<typeof fetch>) => {
      const response = await immediateFetch(...args);
      if (String(args[0]) === "/users/me" && args[1]?.method === "PATCH") {
        return new Promise<Response>((resolve) => { finishSave = () => resolve(response); });
      }
      return response;
    }));
    await userEvent.click(screen.getByRole("button", { name: "Зберегти налаштування" }));
    await waitFor(() => expect(finishSave).toBeTypeOf("function"));
    await userEvent.click(screen.getByRole("button", { name: "вийти" }));
    const login = await screen.findByRole("button", { name: "Увійти через Telegram" });
    user = { ...user, id: 2, name: "Інший профіль", telegram_id: 99 };
    vi.spyOn(window, "open").mockReturnValue(null);
    await userEvent.click(login);
    await screen.findByText("Інший профіль", {}, { timeout: 4000 });
    await act(async () => { finishSave(); });
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(client.getQueryData(["me"])).toMatchObject({ id: 2, name: "Інший профіль" });
    expect(screen.getByText("Інший профіль")).toBeInTheDocument();
  });

  it("підтвердження Telegram відкриває звички", async () => {
    signedIn = false;
    vi.spyOn(window, "open").mockReturnValue(null);
    openApp();
    await userEvent.click(await screen.findByRole("button", { name: "Увійти через Telegram" }));
    expect(await screen.findByRole("link", { name: "Відкрити бота" })).toHaveAttribute("href", "https://t.me/example?start=login-token");
    expect(await screen.findByText("Йога", {}, { timeout: 4000 })).toBeInTheDocument();
  });
});

describe("Розклад і профіль", () => {
  it("сьогодні та прогрес враховують дату користувача і лише заплановані звички", async () => {
    today = "2026-09-11";
    habits = [makeHabit(1, "Йога", [4]), makeHabit(2, "Читання", [0])];
    habits.forEach((h) => { h.start_date = "2026-09-01"; });
    openApp();
    expect(await screen.findByText("Сьогодні: 11 вер")).toBeInTheDocument();
    expect(await screen.findByText("0 з 1 сьогодні")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Інші активні звички" })).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Йога: не зроблено сьогодні"));
    await waitFor(() => expect(calls.find((call) => call.method === "POST" && call.path === "/habits/1/checkins")?.body).toEqual({ day: today }));
  });

  it("створює звичку з датою початку та вибраними днями", async () => {
    openApp();
    await screen.findByText("Йога");
    // Коли звички вже є, форма згорнута в кнопку й займає один рядок.
    expect(screen.queryByRole("textbox", { name: "Назва звички" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Нова звичка" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Назва звички" }), "  Біг  ");
    const date = screen.getByLabelText("Дата початку");
    expect(date).toHaveValue("2026-09-12");
    expect(date).toHaveAttribute("max", "2026-09-12");
    for (const label of ["Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]) await userEvent.click(screen.getByRole("checkbox", { name: label }));
    await userEvent.click(screen.getByRole("button", { name: "Додати" }));
    await waitFor(() => expect(calls.find((call) => call.path === "/habits" && call.method === "POST")?.body).toMatchObject({ name: "Біг", start_date: "2026-09-12", weekdays: [0] }));
    expect(await screen.findByText("Біг")).toBeInTheDocument();
    // Після успіху форма згортається, а фокус лишається на кнопці, а не губиться.
    const toggle = screen.getByRole("button", { name: "Нова звичка" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("зберігає налаштування нагадувань і пропонує експорт", async () => {
    openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Налаштування" }));
    const name = screen.getByRole("textbox", { name: "Ім’я" });
    await userEvent.clear(name);
    await userEvent.type(name, "Марія");
    const zone = screen.getByRole("combobox", { name: "Часовий пояс" });
    await userEvent.clear(zone);
    await userEvent.type(zone, "Europe/London");
    await userEvent.selectOptions(screen.getByLabelText("Година нагадування"), "8");
    await userEvent.click(screen.getByRole("checkbox", { name: "Нагадування в Telegram" }));
    await userEvent.click(screen.getByRole("button", { name: "Зберегти налаштування" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Налаштування збережено");
    expect(calls.find((call) => call.path === "/users/me" && call.method === "PATCH")?.body).toEqual({ name: "Марія", timezone: "Europe/London", reminder_hour: 8, reminders_enabled: false });
    expect(screen.getByRole("link", { name: "Завантажити мої дані (JSON)" })).toHaveAttribute("href", "/users/me/export");
  });

  it("дозволяє безіменному профілю зберегти нагадування без зміни імені", async () => {
    user.name = "";
    openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Налаштування" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Нагадування в Telegram" }));
    await userEvent.click(screen.getByRole("button", { name: "Зберегти налаштування" }));
    await screen.findByRole("status");
    expect(calls.find((call) => call.path === "/users/me" && call.method === "PATCH")?.body).not.toHaveProperty("name");
  });

  it("пояснює, чому не можна замінити наявне ім’я пробілами", async () => {
    openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Налаштування" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Ім’я" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Ім’я" }), "   ");
    expect(screen.getByRole("textbox", { name: "Ім’я" })).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Ім’я не може бути порожнім.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Зберегти налаштування" })).toBeDisabled();
  });
});

describe("Історія, архів та аналітика", () => {
  it("не подає період без запланованих днів як нульовий результат", async () => {
    habits = [makeHabit(1, "Йога", [0])];
    openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Аналітика" }));
    const label = await screen.findByText("за розкладом за 30 днів");
    expect(within(label.parentElement!).getByText("—")).toBeInTheDocument();
    expect(screen.getByText("У цьому періоді ще немає запланованих днів.")).toBeInTheDocument();
  });

  it("архівує і відновлює звичку разом з її історією", async () => {
    openApp();
    // Архівування — рідкісна дія, тому живе в розгорнутій картці.
    await userEvent.click(await screen.findByLabelText("Показати історію та дії"));
    await userEvent.click(await screen.findByRole("button", { name: "Архівувати «Йога»" }));
    await waitFor(() => expect(screen.queryByText("Йога")).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("tab", { name: "Архів" }));
    expect(await screen.findByText("Йога")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Відновити «Йога»" }));
    expect(await screen.findByText("Архів порожній.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Сьогодні" }));
    expect(await screen.findByText("Йога")).toBeInTheDocument();
  });

  it("помилка календаря має повторення, а після нього можна додати й зняти минулий день", async () => {
    failPath = "/habits/1/checkins";
    openApp();
    await userEvent.click(await screen.findByLabelText("Показати історію та дії"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Тимчасова помилка");
    expect(screen.queryByRole("group", { name: "Відмітки за датами" })).not.toBeInTheDocument();
    failPath = null;
    await userEvent.click(screen.getByRole("button", { name: "Спробувати ще раз" }));
    await userEvent.click(await screen.findByRole("button", { name: /2026-09-11.*відмітити/ }));
    const done = await screen.findByRole("button", { name: /2026-09-11.*зроблено/ });
    await waitFor(() => expect(done).toBeEnabled());
    await userEvent.click(done);
    await waitFor(() => expect(checkins).toHaveLength(0));
    expect(calls.some((call) => call.method === "DELETE" && call.path.endsWith("/checkins/2026-09-11"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Попередні 12 тижнів" }));
    await waitFor(() => expect(calls.some((call) => call.path.includes("since=2026-02-22") && call.path.includes("until=2026-06-21"))).toBe(true));
  });

  it("аналітика показує помилку історії з повторенням і рахує весь час з архівом", async () => {
    habits.push({ ...makeHabit(2, "Стара звичка"), archived_at: "2026-09-12" });
    failPath = "/habits/1/checkins";
    openApp();
    await userEvent.click(await screen.findByRole("tab", { name: "Аналітика" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Тимчасова помилка");
    expect(screen.queryByText("відміток за весь час")).not.toBeInTheDocument();
    failPath = null;
    await userEvent.click(screen.getByRole("button", { name: "Спробувати ще раз" }));
    const label = await screen.findByText("відміток за весь час");
    expect(within(label.parentElement!).getByText("200")).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/stats?include_archived=true")).toBe(true);
  });
});
