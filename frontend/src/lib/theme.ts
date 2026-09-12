/**
 * Світла/темна тема.
 *
 * Механізм навмисно простий: увесь колір у CSS заданий змінними
 * (`--bg`, `--text`, …), а перемикання теми — це заміна одного атрибута
 * `data-theme` на `<html>`. CSS далі сам підставляє інший набір значень.
 * React про кольори не знає нічого й перемальовувати нічого не мусить.
 *
 * Головне розділення в цьому файлі: ПОКАЗАТИ тему і ЗАПАМ'ЯТАТИ вибір —
 * різні дії, і вони навмисно розведені по різних функціях. Спершу вони
 * були склеєні в одній, і це давало неочевидну ваду: сам факт відкриття
 * сторінки записував тему у сховище, ніби людина її обрала. Після цього
 * системне налаштування переставало на щось впливати назавжди.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "habits-theme";

/** Збережений вибір або `null`, якщо людина нічого не обирала. */
export function savedTheme(): Theme | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === "light" || saved === "dark" ? saved : null;
  } catch {
    // Приватний режим або заборонені cookie: localStorage кидає виняток
    // навіть на читання. Не привід ронити застосунок.
    return null;
  }
}

/**
 * Яку тему показувати зараз.
 *
 * Пріоритет: свідомий вибір людини важливіший за налаштування системи.
 * Хто обрав світлу тему на темному ноутбуці — отримає світлу.
 */
export function readTheme(): Theme {
  return savedTheme() ?? (prefersDark() ? "dark" : "light");
}

export function prefersDark(): boolean {
  return darkQuery()?.matches ?? false;
}

/** `null` у середовищах без matchMedia — трапляється в тестах. */
function darkQuery(): MediaQueryList | null {
  if (typeof window.matchMedia !== "function") return null;
  return window.matchMedia("(prefers-color-scheme: dark)");
}

/**
 * Стежити за перемиканням теми в системі.
 *
 * Має сенс лише поки людина не зробила власного вибору: після нього
 * системні зміни ігноруються. Повертає функцію відписки.
 */
export function watchSystemTheme(onChange: (theme: Theme) => void): () => void {
  const query = darkQuery();
  if (query === null) return () => {};

  const handler = (event: MediaQueryListEvent) => {
    onChange(event.matches ? "dark" : "light");
  };

  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/** Показати тему. Нічого не запам'ятовує — лише чіпає DOM. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;

  // color-scheme повідомляє браузеру, якими малювати ЙОГО власні
  // елементи: смуги прокрутки, поля вводу, календарик у date-полі.
  // Без цього рядка на темному тлі лишається сліпучо-біла прокрутка.
  document.documentElement.style.colorScheme = theme;
}

/** Запам'ятати ВИБІР. Викликається тільки у відповідь на дію людини. */
export function saveTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Не змогли зберегти — тема все одно застосована до кінця сеансу.
  }
}

export function nextTheme(current: Theme): Theme {
  return current === "dark" ? "light" : "dark";
}
