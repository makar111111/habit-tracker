import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Calendar } from "./Calendar";

const today = new Date(2026, 8, 12);

describe("Підписи місяців", () => {
  it("підписує лише перший тиждень кожного місяця", () => {
    // 6 тижнів до 13 вересня 2026 починаються з понеділка 3 серпня:
    // п'ять тижнів серпня, потім тиждень із 7 вересня.
    const { container } = render(
      <Calendar today={today} weeks={6} doneDays={new Set()} onToggle={vi.fn()} />,
    );
    const labels = [...container.querySelectorAll(".calendar-months span")].map(
      (span) => span.textContent,
    );
    expect(labels).toEqual(["сер", "", "", "", "", "вер"]);
  });
});

describe("Редагування календаря", () => {
  it("дозволяє ставити та знімати минулі відмітки й блокує майбутні", async () => {
    const onToggle = vi.fn();
    render(<Calendar today={today} doneDays={new Set(["2026-09-11"])} onToggle={onToggle} />);
    await userEvent.click(screen.getByRole("button", { name: /2026-09-11.*зроблено/ }));
    expect(onToggle).toHaveBeenLastCalledWith("2026-09-11", true);
    await userEvent.click(screen.getByRole("button", { name: /2026-09-10.*пропуск/ }));
    expect(onToggle).toHaveBeenLastCalledWith("2026-09-10", false);
    expect(screen.getByRole("button", { name: /2026-09-13/ })).toBeDisabled();
  });

  it("позначає вихідний і дату до початку без заборони заднього числа", () => {
    render(
      <Calendar
        today={today}
        doneDays={new Set()}
        onToggle={vi.fn()}
        habit={{ start_date: "2026-09-10", weekdays: [0, 2, 4], archived_at: null }}
      />,
    );
    expect(screen.getByRole("button", { name: /2026-09-12.*поза розкладом/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /2026-09-09.*до початку/ })).toBeEnabled();
  });

  it("блокує дні після архівування та всі повторні натискання під час запису", () => {
    const { rerender } = render(
      <Calendar
        today={today}
        doneDays={new Set()}
        onToggle={vi.fn()}
        habit={{ start_date: null, weekdays: [0, 1, 2, 3, 4, 5, 6], archived_at: "2026-09-10" }}
      />,
    );
    expect(screen.getByRole("button", { name: /2026-09-11/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /2026-09-10/ })).toBeEnabled();
    rerender(<Calendar today={today} doneDays={new Set()} onToggle={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: /2026-09-10/ })).toBeDisabled();
  });

  it("дозволяє зняти вже наявну відмітку після архівування", async () => {
    const onToggle = vi.fn();
    render(
      <Calendar
        today={today}
        doneDays={new Set(["2026-09-11"])}
        onToggle={onToggle}
        habit={{ archived_at: "2026-09-10" }}
      />,
    );
    const done = screen.getByRole("button", { name: /2026-09-11.*зроблено.*зняти відмітку/ });
    expect(done).toBeEnabled();
    await userEvent.click(done);
    expect(onToggle).toHaveBeenCalledWith("2026-09-11", true);
  });

  it("дозволяє зняти наявну минулу відмітку після дати архівування", async () => {
    const onToggle = vi.fn();
    render(
      <Calendar
        today={today}
        doneDays={new Set(["2026-09-11"])}
        onToggle={onToggle}
        habit={{ start_date: null, weekdays: [0, 1, 2, 3, 4, 5, 6], archived_at: "2026-09-10" }}
      />,
    );
    const done = screen.getByRole("button", { name: /2026-09-11.*зроблено.*зняти відмітку/ });
    expect(done).toBeEnabled();
    expect(done).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(done);
    expect(onToggle).toHaveBeenCalledWith("2026-09-11", true);
    expect(screen.getByRole("button", { name: /2026-09-12.*після архівування/ })).toBeDisabled();
  });
});
