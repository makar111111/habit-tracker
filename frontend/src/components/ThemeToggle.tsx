import { useEffect, useState } from "react";

import {
  applyTheme,
  nextTheme,
  readTheme,
  saveTheme,
  savedTheme,
  watchSystemTheme,
  type Theme,
} from "../lib/theme";

/**
 * Перемикач світлої й темної теми.
 *
 * Початкове значення читаємо ЛІНИВО — через функцію в useState, а не
 * `useState(readTheme())`. Різниця в тому, що аргумент обчислювався б
 * на кожному перемальовуванні компонента, хоч використовується лише
 * при першому.
 *
 * Запис у сховище живе ТІЛЬКИ в обробнику кліку. Раніше він стояв
 * в ефекті поряд із `applyTheme`, і це давало тихий дефект: сам факт
 * відкриття сторінки зберігав тему, ніби людина її обрала. Після цього
 * перемикання теми в системі на застосунок уже не впливало — назавжди.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);

  // Ефект лише СИНХРОНІЗУЄ DOM зі станом. Жодних побічних записів:
  // він спрацьовує і тоді, коли ніхто нічого не вибирав.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Поки власного вибору немає — йдемо за системою наживо. Людина
  // перемкнула тему в налаштуваннях Windows — застосунок перемкнувся
  // теж, без перезавантаження сторінки.
  useEffect(() => {
    if (savedTheme() !== null) return;
    return watchSystemTheme(setTheme);
  }, []);

  function toggle() {
    const next = nextTheme(theme);
    setTheme(next);
    saveTheme(next);
  }

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      className="icon-button"
      onClick={toggle}
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
