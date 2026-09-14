import { render } from "@testing-library/react";
import { Bar, BarChart, Cell, XAxis, YAxis } from "recharts";
import { describe, expect, it } from "vitest";

import { DAILY_BAR_GAP } from "./AnalyticsScreen";

/**
 * Поведінка Recharts, на якій тримається «Динаміка».
 *
 * Графік має розрізняти три стани дня: частку виконання, нуль і «нічого не
 * заплановано». Нуль малюється коротким сірим штрихом (minPointSize), а null
 * не малюється зовсім. Це властивість бібліотеки, а не нашого коду, тому її
 * й перевіряємо: якщо оновлення Recharts почне малювати null або ігнорувати
 * minPointSize, 0% і вихідний знову тихо зіллються в однакову порожнечу.
 */
describe("Recharts: нуль і відсутність значення", () => {
  it("нуль видно штрихом, а null не малюється", () => {
    const data = [
      { label: "a", percent: 50 },
      { label: "b", percent: 0 },
      { label: "c", percent: null },
    ];
    const { container } = render(
      <BarChart width={300} height={200} data={data}>
        <XAxis dataKey="label" />
        <YAxis domain={[0, 100]} />
        <Bar dataKey="percent" minPointSize={3} isAnimationActive={false}>
          {data.map((point) => (
            <Cell key={point.label} fill={point.percent === 0 ? "gray" : "blue"} />
          ))}
        </Bar>
      </BarChart>,
    );

    const bars = [...container.querySelectorAll(".recharts-bar-rectangle path")];
    expect(bars.map((bar) => bar.getAttribute("fill"))).toEqual(["blue", "gray"]);
    expect(Number(bars[1].getAttribute("height"))).toBe(3);
  });
});

describe("Recharts: щільна «Динаміка» на телефоні", () => {
  function barWidth(points: number, chartWidth: number) {
    const data = Array.from({ length: points }, (_, i) => ({ label: String(i), percent: 60 }));
    const { container } = render(
      <BarChart
        width={chartWidth}
        height={200}
        data={data}
        margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
        barCategoryGap={DAILY_BAR_GAP}
      >
        <XAxis dataKey="label" />
        <YAxis width={48} domain={[0, 100]} />
        <Bar dataKey="percent" isAnimationActive={false} />
      </BarChart>,
    );
    return Number(container.querySelector(".recharts-bar-rectangle path")?.getAttribute("width"));
  }

  it.each([
    // Ширина самого графіка, виміряна в браузері: 309 px на екрані 375 px.
    ["375 px", 309],
    // Найвужчий поширений телефон, 320 px: на 55 px вужче.
    ["320 px", 254],
  ])("90 днів на екрані %s лишаються видимими стовпчиками", (_label, chartWidth) => {
    // З фіксованим проміжком 2 px на 375 px виходило −1,17 px: графік був порожнім.
    expect(barWidth(90, chartWidth)).toBeGreaterThanOrEqual(1);
  });
});
