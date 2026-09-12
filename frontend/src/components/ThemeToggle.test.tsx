import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "./ThemeToggle";

/**
 * Слухачі зміни системної теми, зібрані вручну — щоб тест міг
 * «перемкнути тему в системі» й подивитися, чи застосунок відреагує.
 */
let systemListeners: Array<(event: MediaQueryListEvent) => void> = [];

function mockSystem(dark: boolean) {
  systemListeners = [];
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: dark && query.includes("dark"),
    media: query,
    onchange: null,
    addEventListener: (_: string, handler: (e: MediaQueryListEvent) => void) => {
      systemListeners.push(handler);
    },
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

function switchSystemTo(dark: boolean) {
  // act() обов'язковий: подія приходить ззовні React, і без нього
  // стан оновиться, а перемальовування до перевірки не дійде —
  // тест побачить старе значення й впаде на рівному місці.
  act(() => {
    for (const listener of systemListeners) {
      listener({ matches: dark } as MediaQueryListEvent);
    }
  });
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  mockSystem(true);
});

describe("ThemeToggle", () => {
  it("НЕ запам'ятовує тему, поки людина нічого не обрала", () => {
    // Саме цей дефект тут і закритий. Раніше запис у сховище стояв
    // в ефекті поряд із застосуванням теми — і сам факт відкриття
    // сторінки прив'язував людину до того, що система казала в ту
    // мить. Назавжди: далі системне налаштування вже не впливало.
    render(<ThemeToggle />);
    expect(localStorage.getItem("habits-theme")).toBeNull();
  });

  it("показує системну тему при першому відкритті", () => {
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("запам'ятовує тему після кліку", async () => {
    render(<ThemeToggle />);

    await userEvent.click(screen.getByRole("button"));

    expect(localStorage.getItem("habits-theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("слідує за системою, поки вибору немає", () => {
    render(<ThemeToggle />);
    expect(document.documentElement.dataset.theme).toBe("dark");

    switchSystemTo(false);

    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("ігнорує систему після свідомого вибору", async () => {
    localStorage.setItem("habits-theme", "light");
    render(<ThemeToggle />);

    switchSystemTo(true);

    // Людина обрала світлу — системна темрява цього не скасовує.
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("підказує дикторові, що саме зробить кнопка", () => {
    render(<ThemeToggle />);
    // Усередині лише емодзі, тож без aria-label диктор прочитав би
    // «сонце» й людина не дізналася б, що це перемикач.
    expect(screen.getByRole("button")).toHaveAccessibleName("Увімкнути світлу тему");
  });
});
