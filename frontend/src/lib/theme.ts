/**
 * Світла/темна тема.
 *
 * Механізм навмисно простий: увесь колір у CSS заданий змінними
 * (`--bg`, `--text`, …), а перемикання теми — це заміна одного атрибута
 * `data-theme` на `<html>`. CSS далі сам підставляє інший набір значень.
 * React про кольори не знає нічого й перемальовувати нічого не мусить.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "habits-theme";

/**
 * Прочитати збережений вибір, а якщо його немає — спитати систему.
 *
 * Різниця принципова: «людина натиснула перемикач» важливіше за
 * «в системі темно». Тому збережене значення має пріоритет, і людина,
 * яка свідомо обрала світлу тему на темному ноутбуці, отримає світлу.
 */
export function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Приватний режим або заборонені cookie: localStorage кидає виняток
    // навіть на читання. Не привід ронити застосунок.
  }

  return prefersDark() ? "dark" : "light";
}

export function prefersDark(): boolean {
  // matchMedia немає в дуже старих браузерах і в частині тестових
  // середовищ, тому перевіряємо саму наявність функції.
  return typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Застосувати тему до сторінки й запам'ятати вибір. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;

  // color-scheme повідомляє браузеру, якими малювати ЙОГО власні
  // елементи: смуги прокрутки, поля вводу, календарик у date-полі.
  // Без цього рядка на темному тлі лишається сліпучо-біла прокрутка.
  document.documentElement.style.colorScheme = theme;

  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Не змогли зберегти — тема все одно застосована до кінця сеансу.
  }
}

export function nextTheme(current: Theme): Theme {
  return current === "dark" ? "light" : "dark";
}
