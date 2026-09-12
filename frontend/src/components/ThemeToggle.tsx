import { useEffect, useState } from "react";

import { applyTheme, nextTheme, readTheme, type Theme } from "../lib/theme";

/**
 * Перемикач світлої й темної теми.
 *
 * Початкове значення читаємо ЛІНИВО — через функцію в useState, а не
 * `useState(readTheme())`. Різниця в тому, що аргумент обчислювався б
 * на кожному перемальовуванні компонента, хоч використовується лише
 * при першому. Для читання з localStorage це зайвий похід у сховище
 * при кожному кліку по будь-якій кнопці застосунку.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      className="icon-button"
      onClick={() => setTheme(nextTheme)}
      // aria-label потрібен, бо всередині кнопки лише емодзі. Екранний
      // диктор прочитав би "сонце" — і людина не дізналася б, що це
      // взагалі перемикач і що саме він зробить.
      aria-label={isDark ? "Увімкнути світлу тему" : "Увімкнути темну тему"}
      title={isDark ? "Світла тема" : "Темна тема"}
    >
      {isDark ? "☀" : "☾"}
    </button>
  );
}
