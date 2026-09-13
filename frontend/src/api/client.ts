/**
 * Обгортка над fetch: одна точка, через яку фронтенд говорить з API.
 *
 * Навіщо обгортка, а не голий fetch по місцях: інакше в кожному
 * компоненті довелося б повторювати перевірку `response.ok`, розбір JSON
 * і витягування поля `detail` з помилки FastAPI. Повторення — це місце,
 * де рано чи пізно щось забудуть.
 */

import type {
  Checkin,
  CreateHabitInput,
  Habit,
  HabitStats,
  LoginCode,
  LoginPoll,
  User,
  UserChanges,
} from "./types";

/**
 * Помилка з відповіді сервера.
 *
 * Успадковуємо від Error, а не кидаємо звичайний об'єкт: так працює
 * `instanceof`, а стек викликів лишається читабельним у консолі.
 * Поле `status` дозволяє розрізняти випадки — 401 означає «покажи екран
 * входу», а 409 при відмітці означає «вже відмічено», що взагалі не біда.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Чи це саме «ти не увійшов», а не якась інша халепа. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const { method = "GET", body } = options;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      // Cookie сесії позначена httpOnly, тож JavaScript її не бачить —
      // але браузер сам прикладає її до запиту. "same-origin" — це явна
      // згода на таку поведінку. У режимі розробки походження збігаються
      // завдяки проксі Vite (див. vite.config.ts).
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    // Сюди потрапляємо, коли сервера немає взагалі: fetch кидає TypeError
    // ще до будь-якої відповіді. Статусу тут не існує, тому 0 —
    // домовленість «зв'язку не було».
    throw new ApiError(0, "Сервер не відповідає. Він точно запущений?");
  }

  if (response.status === 204) {
    // 204 No Content — тіла немає за визначенням. Спроба розібрати його
    // як JSON впала б на порожньому рядку.
    return undefined as T;
  }

  if (!response.ok) {
    throw new ApiError(response.status, await extractMessage(response));
  }

  return (await response.json()) as T;
}

/**
 * Дістати людське пояснення з невдалої відповіді.
 *
 * FastAPI кладе його в поле `detail`. Але воно буває двох видів: рядок
 * (коли ми самі кинули HTTPException) або масив описів полів (коли
 * валідація pydantic не пропустила тіло запиту). Другий випадок треба
 * зібрати в речення вручну — інакше користувач побачить `[object Object]`.
 */
async function extractMessage(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    const detail = (data as { detail?: unknown }).detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
      const parts = detail
        .map((item: { msg?: unknown }) => (typeof item.msg === "string" ? item.msg : null))
        .filter((msg): msg is string => msg !== null);
      if (parts.length > 0) return parts.join("; ");
    }
  } catch {
    // Тіло не JSON — буває на 500 від проксі чи на HTML-сторінці помилки.
  }

  return `Помилка ${response.status}`;
}

// ---------- Звички ----------

export function listHabits(includeArchived = false): Promise<Habit[]> {
  return request<Habit[]>(`/habits${includeArchived ? "?include_archived=true" : ""}`);
}

export function listStats(includeArchived = false): Promise<HabitStats[]> {
  return request<HabitStats[]>(`/stats${includeArchived ? "?include_archived=true" : ""}`);
}

export function createHabit(input: CreateHabitInput): Promise<Habit> {
  return request<Habit>("/habits", { method: "POST", body: input });
}

export function updateHabit(
  id: number,
  changes: { name?: string; description?: string; archived?: boolean },
): Promise<Habit> {
  return request<Habit>(`/habits/${id}`, { method: "PATCH", body: changes });
}

export function deleteHabit(id: number): Promise<void> {
  return request<void>(`/habits/${id}`, { method: "DELETE" });
}

// ---------- Відмітки ----------

export function listCheckins(
  habitId: number,
  range?: { since?: string; until?: string },
): Promise<Checkin[]> {
  // URLSearchParams сам подбає про екранування і не додасть зайвого "?",
  // якщо параметрів немає.
  const query = new URLSearchParams();
  if (range?.since) query.set("since", range.since);
  if (range?.until) query.set("until", range.until);

  const suffix = query.size > 0 ? `?${query}` : "";
  return request<Checkin[]>(`/habits/${habitId}/checkins${suffix}`);
}

/**
 * Відмітити день.
 *
 * 409 означає «цей день уже відмічено». Для нас це не помилка, а
 * підтвердження бажаного стану: людина двічі тицьнула по кнопці або
 * вкладка відстала від життя. Повертаємо `false` замість винятку —
 * інакше кожен виклик довелося б обгортати в try/catch заради
 * ситуації, яку й лагодити не треба.
 */
export async function checkIn(habitId: number, day?: string): Promise<boolean> {
  try {
    await request<Checkin>(`/habits/${habitId}/checkins`, {
      method: "POST",
      body: { day: day ?? null },
    });
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return false;
    throw error;
  }
}

/** Зняти відмітку. 404 тут так само означає «вже і так немає». */
export async function undoCheckIn(habitId: number, day: string): Promise<boolean> {
  try {
    await request<void>(`/habits/${habitId}/checkins/${day}`, { method: "DELETE" });
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return false;
    throw error;
  }
}

// ---------- Користувач і вхід ----------

export function getMe(): Promise<User> {
  return request<User>("/users/me");
}

export function getToday(): Promise<{ day: string }> {
  return request<{ day: string }>("/users/me/today");
}

export function updateMe(changes: UserChanges): Promise<User> {
  return request<User>("/users/me", { method: "PATCH", body: changes });
}

export function requestLoginCode(): Promise<LoginCode> {
  return request<LoginCode>("/auth/login-code", { method: "POST" });
}

export function pollLoginCode(token: string): Promise<LoginPoll> {
  return request<LoginPoll>(`/auth/login-code/${token}`);
}

export function logout(): Promise<void> {
  return request<void>("/auth/logout", { method: "POST" });
}
