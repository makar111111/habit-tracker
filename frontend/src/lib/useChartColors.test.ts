import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChartColors } from "./useChartColors";

/**
 * Палітра в тесті задається справжнім <style>, а не підміною
 * getComputedStyle: так перевіряється той самий шлях, що в браузері —
 * змінна в CSS → обчислений стиль → рядок кольору.
 */
const PALETTE = `
  :root {
    --accent: #111111; --success: #222222; --muted: #333333;
    --border: #444444; --text: #555555; --card: #666666;
  }
  :root[data-theme="dark"] { --accent: #eeeeee; --text: #dddddd; }
`;

let style: HTMLStyleElement;

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  style = document.createElement("style");
  style.textContent = PALETTE;
  document.head.append(style);
});

afterEach(() => {
  style.remove();
  vi.restoreAllMocks();
});

describe("useChartColors", () => {
  it("віддає готові кольори, а не var(...)", () => {
    // Увесь сенс хука: у SVG-атрибут stroke має потрапити "#111111",
    // бо "var(--accent)" там браузер не розкриє і лінія не намалюється.
    const { result } = renderHook(() => useChartColors());

    expect(result.current).toEqual({
      accent: "#111111",
      success: "#222222",
      muted: "#333333",
      border: "#444444",
      text: "#555555",
      card: "#666666",
    });
  });

  it("обрізає пробіли навколо значення змінної", () => {
    // Браузер віддає значення змінної разом із пробілами навколо, а jsdom
    // обрізає їх сам — тож через <style> тест пройшов би й без trim().
    // Тому тут значення з пробілами підкладається явно.
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: () => "  #abcdef ",
    } as unknown as CSSStyleDeclaration);
    const { result } = renderHook(() => useChartColors());

    expect(result.current.accent).toBe("#abcdef");
  });

  it("перечитує палітру, коли змінюється data-theme", async () => {
    const { result } = renderHook(() => useChartColors());

    // Колбек MutationObserver асинхронний, тому чекаємо через waitFor.
    act(() => document.documentElement.setAttribute("data-theme", "dark"));

    await waitFor(() => expect(result.current.accent).toBe("#eeeeee"));
    expect(result.current.text).toBe("#dddddd");
    // Змінні, яких темна тема не перевизначає, лишаються зі світлої.
    expect(result.current.card).toBe("#666666");
  });

  it("не реагує на інші атрибути <html>", async () => {
    const spy = vi.spyOn(window, "getComputedStyle");
    renderHook(() => useChartColors());
    const callsAfterMount = spy.mock.calls.length;

    act(() => document.documentElement.setAttribute("lang", "uk"));
    // Даємо спостерігачу шанс спрацювати, якби фільтр не працював.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(spy.mock.calls.length).toBe(callsAfterMount);
    document.documentElement.removeAttribute("lang");
  });

  it("відписується від спостереження при демонтажі", () => {
    const disconnect = vi.spyOn(MutationObserver.prototype, "disconnect");
    const { unmount } = renderHook(() => useChartColors());

    unmount();

    // Без відписки кожне відкриття аналітики лишало б живого
    // спостерігача, що смикає setState у демонтованому компоненті.
    expect(disconnect).toHaveBeenCalledTimes(1);
  });
});
