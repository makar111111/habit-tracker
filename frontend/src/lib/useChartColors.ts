import { useEffect, useState } from "react";

/**
 * Кольори графіків, узяті з тих самих CSS-змінних, що й решта сторінки.
 *
 * Навіщо ця морока замість того, щоб просто написати в графіку
 * `stroke="var(--accent)"`: SVG-атрибути `fill` і `stroke` не є
 * CSS-оголошеннями, і браузер НЕ розкриває в них var(). Атрибут
 * лишився б рядком "var(--accent)", тобто некоректним кольором,
 * і лінія просто не намалювалася б.
 *
 * Тому читаємо обчислені значення змінних у JavaScript і віддаємо
 * готові шістнадцяткові рядки. Альтернатива — продублювати палітру
 * окремим об'єктом у коді — гірша: два джерела правди про колір
 * розійдуться першої ж миті, коли хтось поправить лише одне з них.
 */
export interface ChartColors {
  accent: string;
  success: string;
  muted: string;
  border: string;
  text: string;
  card: string;
}

const VARIABLES: Record<keyof ChartColors, string> = {
  accent: "--accent",
  success: "--success",
  muted: "--muted",
  border: "--border",
  text: "--text",
  card: "--card",
};

function readColors(): ChartColors {
  const style = getComputedStyle(document.documentElement);

  const read = (name: string) => style.getPropertyValue(name).trim();

  return {
    accent: read(VARIABLES.accent),
    success: read(VARIABLES.success),
    muted: read(VARIABLES.muted),
    border: read(VARIABLES.border),
    text: read(VARIABLES.text),
    card: read(VARIABLES.card),
  };
}

export function useChartColors(): ChartColors {
  const [colors, setColors] = useState<ChartColors>(readColors);

  useEffect(() => {
    // MutationObserver стежить за атрибутом data-theme на <html>.
    // Саме його міняє перемикач теми — а отже, це найнадійніший
    // сигнал «палітра щойно змінилася, перечитай». Підписуватись
    // на подію від самого перемикача було б крихкіше: тему може
    // змінити й скрипт в index.html ще до появи React.
    const observer = new MutationObserver(() => setColors(readColors()));

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => observer.disconnect();
  }, []);

  return colors;
}
