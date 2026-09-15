import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  checkIn,
  createHabit,
  deleteHabit,
  getMe,
  getToday,
  isUnauthorized,
  listCheckins,
  listHabits,
  listStats,
  logout,
  pollLoginCode,
  requestLoginCode,
  undoCheckIn,
  updateHabit,
  updateMe,
} from "./client";

/**
 * Тести обгортки над fetch.
 *
 * App.test.tsx уже ганяє client.ts опосередковано через фейковий сервер,
 * але там перевіряється екран, а не контракт: який саме виняток летить,
 * що в ньому лежить і що саме пішло в мережу. Тут — навпаки: fetch
 * підмінено, а дивимось лише на вхід і вихід функцій клієнта.
 */

type FetchMock = ReturnType<typeof vi.fn<(input: string, init?: RequestInit) => Promise<Response>>>;
let fetchMock: FetchMock;

/** Відповідь з JSON-тілом — як її віддає FastAPI. */
function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Перехопити виняток, щоб перевірити і клас, і поля. */
async function caught(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Очікували виняток, а проміс виконався успішно");
}

/** Аргументи останнього виклику fetch. */
function lastCall(): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.lastCall ?? [];
  return { url: String(url), init: init ?? {} };
}

beforeEach(() => {
  fetchMock = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiError і isUnauthorized", () => {
  it("ApiError — справжній Error зі статусом, назвою і повідомленням", () => {
    const error = new ApiError(418, "Я чайник");
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.status).toBe(418);
    expect(error.message).toBe("Я чайник");
  });

  it("isUnauthorized впізнає лише ApiError зі статусом 401", () => {
    expect(isUnauthorized(new ApiError(401, "Потрібен вхід"))).toBe(true);
    expect(isUnauthorized(new ApiError(403, "Заборонено"))).toBe(false);
    expect(isUnauthorized(new ApiError(0, "Немає зв'язку"))).toBe(false);
  });

  it("isUnauthorized не ведеться на схожий об'єкт чи інший клас помилки", () => {
    // Перевірка через instanceof, а не через поле: інакше будь-що
    // зі `status: 401` (наприклад, сирий Response) вважалося б виходом.
    expect(isUnauthorized({ status: 401 })).toBe(false);
    expect(isUnauthorized(Object.assign(new Error("x"), { status: 401 }))).toBe(false);
    expect(isUnauthorized(null)).toBe(false);
    expect(isUnauthorized(undefined)).toBe(false);
  });
});

describe("Що надсилається на сервер", () => {
  it("GET без тіла: метод GET, credentials same-origin, без заголовків і тіла", async () => {
    fetchMock.mockResolvedValue(json([]));
    await listHabits();

    const { url, init } = lastCall();
    expect(url).toBe("/habits");
    expect(init.method).toBe("GET");
    // Без same-origin браузер не приклав би httpOnly-cookie сесії.
    expect(init.credentials).toBe("same-origin");
    // Content-Type без тіла зайвий: для GET це ще й «непростий» запит.
    expect(init.headers).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it("запит із тілом додає Content-Type: application/json і серіалізує тіло", async () => {
    fetchMock.mockResolvedValue(json({ id: 1 }, 201));
    const input = { name: "Йога", description: "Зранку" };
    await createHabit(input as Parameters<typeof createHabit>[0]);

    const { url, init } = lastCall();
    expect(url).toBe("/habits");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    // Кирилиця йде тілом JSON, а не заголовком (правило 3 у CLAUDE.md).
    expect(JSON.parse(String(init.body))).toEqual(input);
  });

  it("POST без тіла (вихід, код входу) не шле ні заголовків, ні тіла", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await logout();
    expect(lastCall().url).toBe("/auth/logout");
    expect(lastCall().init).toMatchObject({ method: "POST", credentials: "same-origin" });
    expect(lastCall().init.headers).toBeUndefined();
    expect(lastCall().init.body).toBeUndefined();

    fetchMock.mockResolvedValue(json({ token: "t", url: "https://t.me/x", expires_in: 60 }));
    await requestLoginCode();
    expect(lastCall().url).toBe("/auth/login-code");
    expect(lastCall().init.method).toBe("POST");
    expect(lastCall().init.body).toBeUndefined();
  });

  it("listHabits і listStats додають include_archived лише на прохання", async () => {
    fetchMock.mockImplementation(async () => json([]));

    await listHabits();
    expect(lastCall().url).toBe("/habits");
    await listHabits(true);
    expect(lastCall().url).toBe("/habits?include_archived=true");

    await listStats();
    expect(lastCall().url).toBe("/stats");
    await listStats(true);
    expect(lastCall().url).toBe("/stats?include_archived=true");
  });

  it("updateHabit шле PATCH на /habits/{id} лише зі зміненими полями", async () => {
    fetchMock.mockResolvedValue(json({ id: 7 }));
    await updateHabit(7, { archived: true });

    const { url, init } = lastCall();
    expect(url).toBe("/habits/7");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe('{"archived":true}');
  });

  it("deleteHabit шле DELETE на /habits/{id}", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await deleteHabit(3);
    expect(lastCall().url).toBe("/habits/3");
    expect(lastCall().init.method).toBe("DELETE");
  });

  describe("listCheckins — параметри періоду", () => {
    beforeEach(() => {
      fetchMock.mockImplementation(async () => json([]));
    });

    it("без періоду — без знака питання в кінці", async () => {
      await listCheckins(5);
      expect(lastCall().url).toBe("/habits/5/checkins");
    });

    it("порожні рядки вважаються відсутніми параметрами", async () => {
      await listCheckins(5, { since: "", until: "" });
      expect(lastCall().url).toBe("/habits/5/checkins");
    });

    it("since і until потрапляють у рядок запиту", async () => {
      await listCheckins(5, { since: "2026-05-01", until: "2026-09-12" });
      expect(lastCall().url).toBe("/habits/5/checkins?since=2026-05-01&until=2026-09-12");
    });

    it("можна передати лише одну межу", async () => {
      await listCheckins(5, { until: "2026-09-12" });
      expect(lastCall().url).toBe("/habits/5/checkins?until=2026-09-12");
    });
  });

  it("checkIn без дня шле day: null, щоб сервер узяв «сьогодні» користувача", async () => {
    fetchMock.mockResolvedValue(json({ id: 1, habit_id: 2, day: "2026-09-12" }, 201));
    await checkIn(2);

    const { url, init } = lastCall();
    expect(url).toBe("/habits/2/checkins");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ day: null });
  });

  it("checkIn із днем шле саме цей день", async () => {
    fetchMock.mockResolvedValue(json({ id: 1, habit_id: 2, day: "2026-09-01" }, 201));
    await checkIn(2, "2026-09-01");
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ day: "2026-09-01" });
  });

  it("undoCheckIn шле DELETE на /habits/{id}/checkins/{day}", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await undoCheckIn(2, "2026-09-01");
    expect(lastCall().url).toBe("/habits/2/checkins/2026-09-01");
    expect(lastCall().init.method).toBe("DELETE");
  });

  it("користувацькі ендпоїнти: getMe, getToday, updateMe, pollLoginCode", async () => {
    fetchMock.mockImplementation(async () => json({}));

    await getMe();
    expect(lastCall()).toMatchObject({ url: "/users/me", init: { method: "GET" } });

    await getToday();
    expect(lastCall()).toMatchObject({ url: "/users/me/today", init: { method: "GET" } });

    await updateMe({ reminder_hour: 9 } as Parameters<typeof updateMe>[0]);
    expect(lastCall()).toMatchObject({ url: "/users/me", init: { method: "PATCH" } });
    expect(JSON.parse(String(lastCall().init.body))).toEqual({ reminder_hour: 9 });

    await pollLoginCode("abc123");
    expect(lastCall()).toMatchObject({ url: "/auth/login-code/abc123", init: { method: "GET" } });
  });

  it("сегменти шляху НЕ екрануються: токен зі «/» чи «?» ламає адресу", async () => {
    // Фіксуємо факт, а не бажану поведінку. Зараз токен і день приходять
    // від нашого ж сервера (token_urlsafe, ISO-дата), тож проблеми немає.
    // Але якщо формат колись зміниться — `?` відріже решту шляху.
    fetchMock.mockImplementation(async () => json({}));
    await pollLoginCode("a/b?c");
    expect(lastCall().url).toBe("/auth/login-code/a/b?c");
  });
});

describe("Успішні відповіді", () => {
  it("повертає розібраний JSON", async () => {
    const habits = [{ id: 1, name: "Йога" }];
    fetchMock.mockResolvedValue(json(habits));
    await expect(listHabits()).resolves.toEqual(habits);
  });

  it("201 Created теж успіх і повертає тіло", async () => {
    fetchMock.mockResolvedValue(json({ id: 10, name: "Біг" }, 201));
    await expect(
      createHabit({ name: "Біг" } as Parameters<typeof createHabit>[0]),
    ).resolves.toEqual({ id: 10, name: "Біг" });
  });

  it("204 No Content повертає undefined і не пробує читати тіло", async () => {
    const response = new Response(null, { status: 204 });
    const readJson = vi.spyOn(response, "json");
    fetchMock.mockResolvedValue(response);

    await expect(deleteHabit(1)).resolves.toBeUndefined();
    // Порожнє тіло як JSON — це SyntaxError; 204 мусить обходити розбір.
    expect(readJson).not.toHaveBeenCalled();
  });

  it("успішна відповідь із некоректним JSON кидає сирий SyntaxError, а не ApiError", async () => {
    // Знахідка: гілка `response.json()` для 2xx не обгорнута. Такий виняток
    // не пройде `instanceof ApiError`, і errorBus покаже технічний текст
    // браузера замість людського. На практиці буває, коли проксі чи
    // статичний сервер віддає HTML зі статусом 200.
    fetchMock.mockResolvedValue(new Response("<html>не JSON</html>", { status: 200 }));
    const error = await caught(listHabits());
    expect(error).toBeInstanceOf(SyntaxError);
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it("200 з порожнім тілом (не 204) теж падає SyntaxError", async () => {
    // Порожнє тіло «оминає» лише статус 204 — 200 чи 201 без тіла ні.
    fetchMock.mockResolvedValue(new Response("", { status: 200 }));
    await expect(logout()).rejects.toBeInstanceOf(SyntaxError);
  });
});

describe("Невдалі відповіді — ApiError", () => {
  it("401 кидає ApiError зі статусом 401 і текстом із detail", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Потрібен вхід" }, 401));
    const error = await caught(getMe());

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).message).toBe("Потрібен вхід");
    expect(isUnauthorized(error)).toBe(true);
  });

  it("404 для звичайного запиту — ApiError зі статусом 404", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Звичку не знайдено" }, 404));
    const error = await caught(updateHabit(99, { name: "x" }));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "Звичку не знайдено" });
  });

  it("409 для звичайного запиту — ApiError зі статусом 409", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Така звичка вже є" }, 409));
    const error = await caught(createHabit({ name: "Йога" } as Parameters<typeof createHabit>[0]));
    expect(error).toMatchObject({ name: "ApiError", status: 409, message: "Така звичка вже є" });
  });

  it("500 — ApiError із серверним detail", async () => {
    fetchMock.mockResolvedValue(json({ detail: "База даних недоступна" }, 500));
    await expect(listStats()).rejects.toMatchObject({
      status: 500,
      message: "База даних недоступна",
    });
  });

  describe("текст помилки з поля detail", () => {
    it("422 від pydantic: повідомлення полів склеюються через «; »", async () => {
      fetchMock.mockResolvedValue(
        json(
          {
            detail: [
              { loc: ["body", "name"], msg: "Field required", type: "missing" },
              {
                loc: ["body", "weekdays"],
                msg: "List should have at least 1 item",
                type: "too_short",
              },
            ],
          },
          422,
        ),
      );
      await expect(createHabit({} as Parameters<typeof createHabit>[0])).rejects.toMatchObject({
        status: 422,
        message: "Field required; List should have at least 1 item",
      });
    });

    it("елементи масиву без рядкового msg пропускаються", async () => {
      fetchMock.mockResolvedValue(
        json({ detail: [{ loc: ["body"] }, { msg: 42 }, { msg: "Лише це" }] }, 422),
      );
      await expect(createHabit({} as Parameters<typeof createHabit>[0])).rejects.toMatchObject({
        message: "Лише це",
      });
    });

    it("масив без жодного msg — запасний текст «Помилка {статус}»", async () => {
      fetchMock.mockResolvedValue(json({ detail: [{ loc: ["body"] }] }, 422));
      await expect(listHabits()).rejects.toMatchObject({ status: 422, message: "Помилка 422" });
    });

    it("порожній масив detail — запасний текст", async () => {
      fetchMock.mockResolvedValue(json({ detail: [] }, 422));
      await expect(listHabits()).rejects.toMatchObject({ message: "Помилка 422" });
    });

    it("detail-об'єкт (не рядок і не масив) не перетворюється на [object Object]", async () => {
      fetchMock.mockResolvedValue(json({ detail: { code: "limit", msg: "Забагато" } }, 429));
      await expect(listHabits()).rejects.toMatchObject({ status: 429, message: "Помилка 429" });
    });

    it("null серед елементів масиву губить і решту повідомлень", async () => {
      // Знахідка: `item.msg` на null кидає TypeError усередині map, його
      // ловить загальний catch — і замість «Лише це» людина бачить
      // «Помилка 422». FastAPI такого не віддає, але розбір не такий
      // стійкий, як виглядає.
      fetchMock.mockResolvedValue(json({ detail: [null, { msg: "Лише це" }] }, 422));
      await expect(listHabits()).rejects.toMatchObject({ message: "Помилка 422" });
    });

    it("JSON без поля detail — запасний текст", async () => {
      fetchMock.mockResolvedValue(json({ error: "щось" }, 400));
      await expect(listHabits()).rejects.toMatchObject({ status: 400, message: "Помилка 400" });
    });

    it("тіло — JSON null — не зронює розбір, а дає запасний текст", async () => {
      fetchMock.mockResolvedValue(json(null, 500));
      await expect(listHabits()).rejects.toMatchObject({ status: 500, message: "Помилка 500" });
    });

    it("тіло не JSON (HTML-сторінка проксі) — ApiError із запасним текстом", async () => {
      fetchMock.mockResolvedValue(
        new Response("<html>502 Bad Gateway</html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
      );
      const error = await caught(listHabits());
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 502, message: "Помилка 502" });
    });

    it("порожнє тіло помилки — ApiError із запасним текстом", async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 503 }));
      await expect(listHabits()).rejects.toMatchObject({ status: 503, message: "Помилка 503" });
    });
  });
});

describe("Мережева помилка", () => {
  it("fetch відхилено — ApiError зі статусом 0 і зрозумілим текстом", async () => {
    // Так поводиться браузер, коли сервер не запущений: TypeError без відповіді.
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await caught(listHabits());

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 0,
      message: "Сервер не відповідає. Він точно запущений?",
    });
    // Статус 0 — не «не увійшов», інакше людину викинуло б на екран входу.
    expect(isUnauthorized(error)).toBe(false);
  });

  it("мережева помилка під час checkIn не маскується під «вже відмічено»", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(checkIn(1)).rejects.toMatchObject({ status: 0 });
  });
});

describe("checkIn — 409 означає «вже відмічено»", () => {
  it("успіх повертає true", async () => {
    fetchMock.mockResolvedValue(json({ id: 1, habit_id: 1, day: "2026-09-12" }, 201));
    await expect(checkIn(1)).resolves.toBe(true);
  });

  it("409 повертає false замість винятку", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Вже відмічено" }, 409));
    await expect(checkIn(1, "2026-09-12")).resolves.toBe(false);
  });

  it("інші помилки (401, 404, 500) прокидаються далі як ApiError", async () => {
    for (const status of [401, 404, 500]) {
      fetchMock.mockResolvedValueOnce(json({ detail: `Статус ${status}` }, status));
      await expect(checkIn(1)).rejects.toMatchObject({ status, message: `Статус ${status}` });
    }
  });
});

describe("undoCheckIn — 404 означає «і так немає»", () => {
  it("успіх (204) повертає true", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(undoCheckIn(1, "2026-09-12")).resolves.toBe(true);
  });

  it("404 повертає false замість винятку", async () => {
    fetchMock.mockResolvedValue(json({ detail: "Відмітки немає" }, 404));
    await expect(undoCheckIn(1, "2026-09-12")).resolves.toBe(false);
  });

  it("інші помилки (401, 409, 500) прокидаються далі як ApiError", async () => {
    // 409 тут — не «вже зроблено»: симетрія з checkIn навмисно відсутня.
    for (const status of [401, 409, 500]) {
      fetchMock.mockResolvedValueOnce(json({ detail: `Статус ${status}` }, status));
      await expect(undoCheckIn(1, "2026-09-12")).rejects.toMatchObject({ status });
    }
  });

  it("404 від зниклої звички теж мовчки дає false", async () => {
    // Факт: клієнт не відрізняє «відмітки немає» від «звички немає» —
    // обидва випадки 404. Для кнопки це прийнятно, але варто знати.
    fetchMock.mockResolvedValue(json({ detail: "Звичку не знайдено" }, 404));
    await expect(undoCheckIn(999, "2026-09-12")).resolves.toBe(false);
  });
});
