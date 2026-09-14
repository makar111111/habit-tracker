import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HabitWithStats } from "../api/types";
import { ProgressPanel } from "./ProgressPanel";

function item(name: string, doneToday: boolean): HabitWithStats {
  return {
    habit: {
      id: name.length,
      name,
      description: "",
      start_date: null,
      weekdays: [0, 1, 2, 3, 4, 5, 6],
      archived_at: null,
    },
    stats: {
      habit_id: name.length,
      total: 1,
      current_streak: 1,
      longest_streak: 1,
      done_today: doneToday,
      last_day: null,
    },
  };
}

describe("ProgressPanel", () => {
  it("показує, скільки звичок зроблено", () => {
    render(<ProgressPanel items={[item("Йога", true), item("Читання", false)]} />);
    expect(screen.getByText("1 з 2 сьогодні")).toBeInTheDocument();
  });

  it("виставляє aria-valuenow у відсотках", () => {
    render(<ProgressPanel items={[item("Йога", true), item("Читання", false)]} />);

    // Шукаємо саме за роллю, а не за класом: так тест перевіряє те,
    // що почує екранний диктор, а не те, як ми назвали div.
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "50");
  });

  it("вітає, коли зроблено все", () => {
    render(<ProgressPanel items={[item("Йога", true)]} />);
    expect(screen.getByText(/усе зроблено/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("нічого не малює без звичок", () => {
    // Смужка «0 з 0» — це не інформація, а шум на порожньому екрані,
    // де людина ще навіть не створила першу звичку.
    const { container } = render(<ProgressPanel items={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
