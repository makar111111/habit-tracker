import { act, renderHook } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearError, reportError, resetErrorBus, useActionError } from "./errorBus";

// Справжній useSyncExternalStore, лише обгорнутий шпигуном: так тест може
// дістати приватну функцію subscribe, яку модуль передає React.
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useSyncExternalStore: vi.fn(actual.useSyncExternalStore) };
});

/** Дістає subscribe, з яким useActionError підписався на сховище. */
function captureSubscribe(): (listener: () => void) => () => void {
  const { unmount } = renderHook(() => useActionError());
  unmount();
  const call = vi.mocked(useSyncExternalStore).mock.lastCall;
  if (!call) throw new Error("useActionError не викликав useSyncExternalStore");
  return call[0];
}

/**
 * Сховище модульне: `current` і список підписників живуть між тестами.
 * Тому кожен тест починає з resetErrorBus — інакше повідомлення з
 * попереднього тесту «протекло» б у наступний і результат залежав би
 * від порядку запуску.
 *
 * `subscribe` назовні не експортується, тож «підписник» тут — компонент
 * (хук useActionError), а «сповіщення» — його перерендер. Лічильник
 * рендерів показує, скільки разів React справді оновив компонент.
 */
function renderErrorHook() {
  const counter = { renders: 0 };
  const hook = renderHook(() => {
    counter.renders += 1;
    return useActionError();
  });
  return { ...hook, counter };
}

beforeEach(() => {
  resetErrorBus();
});

describe("errorBus: публікація", () => {
  it("спочатку помилки немає", () => {
    const { result } = renderErrorHook();

    expect(result.current).toBeNull();
  });

  it("reportError доходить до вже підписаного компонента", () => {
    const { result } = renderErrorHook();

    act(() => reportError("База даних недоступна"));

    expect(result.current).toBe("База даних недоступна");
  });

  it("доходить до кількох підписників одночасно", () => {
    // У застосунку банер один, але сховище не повинно покладатися на це:
    // кожен, хто читає помилку, має побачити той самий знімок.
    const first = renderErrorHook();
    const second = renderErrorHook();

    act(() => reportError("Щось пішло не так"));

    expect(first.result.current).toBe("Щось пішло не так");
    expect(second.result.current).toBe("Щось пішло не так");
  });

  it("компонент, змонтований ПІСЛЯ помилки, одразу її бачить", () => {
    // Сховище пам'ятає останнє повідомлення, а не лише розсилає подію.
    // Тож банер, що з'явився пізніше (наприклад, після перемикання
    // вкладки), не пропускає помилку, яка сталася до його появи.
    reportError("Сталася раніше");

    const { result } = renderErrorHook();

    expect(result.current).toBe("Сталася раніше");
  });

  it("показує лише останнє повідомлення — нове замінює старе", () => {
    const { result } = renderErrorHook();

    act(() => reportError("Перша"));
    act(() => reportError("Друга"));

    expect(result.current).toBe("Друга");
  });

  it("кілька помилок в одному act — перемагає остання", () => {
    // Черги повідомлень немає: це свідоме спрощення, банер показує одне.
    const { result } = renderErrorHook();

    act(() => {
      reportError("Перша");
      reportError("Друга");
      reportError("Третя");
    });

    expect(result.current).toBe("Третя");
  });

  it("однаковий текст удруге не перерендерює компонент", () => {
    // Знімок — рядок, а рядки порівнюються за значенням. Повтор тієї самої
    // помилки не змінює стан, тож React не малює банер вдруге.
    const { result, counter } = renderErrorHook();

    act(() => reportError("Та сама"));
    const rendersAfterFirst = counter.renders;
    act(() => reportError("Та сама"));

    expect(result.current).toBe("Та сама");
    expect(counter.renders).toBe(rendersAfterFirst);
  });

  it("порожній рядок — теж повідомлення, а не «немає помилки»", () => {
    // Відсутність помилки позначається лише null. ErrorBanner перевіряє
    // саме `=== null`, тож "" дасть порожню смужку — фіксуємо поведінку.
    const { result } = renderErrorHook();

    act(() => reportError(""));

    expect(result.current).toBe("");
  });
});

describe("errorBus: очищення", () => {
  it("clearError прибирає повідомлення в підписника", () => {
    const { result } = renderErrorHook();
    act(() => reportError("Помилка"));

    act(() => clearError());

    expect(result.current).toBeNull();
  });

  it("clearError без помилки нічого не ламає і не перерендерює", () => {
    const { result, counter } = renderErrorHook();
    const rendersBefore = counter.renders;

    act(() => clearError());
    act(() => clearError());

    expect(result.current).toBeNull();
    expect(counter.renders).toBe(rendersBefore);
  });

  it("після clearError нова помилка знову доходить", () => {
    const { result } = renderErrorHook();

    act(() => reportError("Перша"));
    act(() => clearError());
    act(() => reportError("Друга"));

    expect(result.current).toBe("Друга");
  });

  it("resetErrorBus скидає збережене повідомлення", () => {
    reportError("Із попереднього сеансу");

    resetErrorBus();

    // Новий компонент не повинен побачити помилку старого сеансу —
    // саме для цього endSession викликає resetErrorBus при виході.
    const { result } = renderErrorHook();
    expect(result.current).toBeNull();
  });

  it("resetErrorBus відв'язує вже змонтованих підписників", () => {
    // Скидання чистить і список слухачів. Змонтований компонент після
    // цього не отримує сповіщень: у застосунку це безпечно, бо endSession
    // водночас переводить на екран входу і ErrorBanner демонтується.
    const { result, counter } = renderErrorHook();

    resetErrorBus();
    const rendersBefore = counter.renders;
    act(() => reportError("Ніхто не почує"));

    expect(counter.renders).toBe(rendersBefore);
    expect(result.current).toBeNull();
  });
});

describe("errorBus: підписка і відписка", () => {
  it("демонтований компонент більше не оновлюється", () => {
    const { result, counter, unmount } = renderErrorHook();
    unmount();
    const rendersAfterUnmount = counter.renders;

    act(() => reportError("Після демонтажу"));

    expect(counter.renders).toBe(rendersAfterUnmount);
    expect(result.current).toBeNull();
  });

  it("відписка одного не зачіпає інших", () => {
    const leaving = renderErrorHook();
    const staying = renderErrorHook();

    leaving.unmount();
    act(() => reportError("Для тих, хто лишився"));

    expect(staying.result.current).toBe("Для тих, хто лишився");
    expect(leaving.result.current).toBeNull();
  });

  it("повторний демонтаж і демонтаж після resetErrorBus не кидають помилок", () => {
    // Функція відписки видаляє з Set — повторне видалення безпечне.
    // Важливо для endSession: resetErrorBus очищає слухачів ДО того,
    // як React демонтує банер і викличе його відписку.
    const first = renderErrorHook();
    const second = renderErrorHook();

    first.unmount();
    expect(() => first.unmount()).not.toThrow();

    resetErrorBus();
    expect(() => second.unmount()).not.toThrow();
  });

  it("функція відписки справді прибирає слухача", () => {
    // Через рендери цього не видно: React сам ігнорує сповіщення для
    // демонтованого компонента, тож забута відписка ховалася б як тихий
    // витік — кожне відкриття екрана лишало б «мертвого» слухача.
    // Тому слухач тут підключається напряму, без React.
    const subscribe = captureSubscribe();
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);

    reportError("Перша");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    reportError("Друга");
    expect(listener).toHaveBeenCalledTimes(1);

    // Повторна відписка — не помилка.
    expect(() => unsubscribe()).not.toThrow();
  });

  it("кожне повідомлення сповіщає слухачів, навіть із тим самим текстом", () => {
    // Відсіювання дублікатів — справа React (порівняння знімків), а не
    // сховища: сховище чесно сповіщає на кожен reportError.
    const subscribe = captureSubscribe();
    const listener = vi.fn();
    subscribe(listener);

    reportError("Та сама");
    reportError("Та сама");

    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("clearError без помилки не сповіщає слухачів", () => {
    const subscribe = captureSubscribe();
    const listener = vi.fn();
    subscribe(listener);

    clearError();
    expect(listener).not.toHaveBeenCalled();

    reportError("Помилка");
    clearError();
    clearError();
    // Одне сповіщення від reportError і одне від першого clearError.
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("слухачі сповіщаються в порядку підписки", () => {
    const subscribe = captureSubscribe();
    const order: string[] = [];
    subscribe(() => order.push("перший"));
    subscribe(() => order.push("другий"));

    reportError("Помилка");

    expect(order).toEqual(["перший", "другий"]);
  });

  it("той самий слухач, підписаний двічі, отримує одне сповіщення", () => {
    // Слухачі зберігаються в Set, тож повторна підписка не дублює виклики.
    const subscribe = captureSubscribe();
    const listener = vi.fn();
    subscribe(listener);
    subscribe(listener);

    reportError("Помилка");

    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("після демонтажу й нового монтування компонент знову отримує помилки", () => {
    renderErrorHook().unmount();

    const { result } = renderErrorHook();
    act(() => reportError("Знову на зв'язку"));

    expect(result.current).toBe("Знову на зв'язку");
  });
});
