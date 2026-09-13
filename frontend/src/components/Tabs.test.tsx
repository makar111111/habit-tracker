import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Tabs } from "./Tabs";

const ITEMS = [
  { value: "today" as const, label: "Сьогодні" },
  { value: "analytics" as const, label: "Аналітика" },
];

function setup(value: "today" | "analytics" = "today") {
  const onChange = vi.fn();
  render(<Tabs items={ITEMS} value={value} onChange={onChange} label="Розділи" />);
  return { onChange };
}

describe("Tabs — ролі", () => {
  it("це справжній набір вкладок, а не група кнопок", () => {
    // Раніше тут були звичайні кнопки з aria-selected. За специфікацією
    // ARIA цей атрибут дозволений лише на ролях tab, option, row,
    // gridcell і treeitem — на кнопці він мовчки ігнорувався, і диктор
    // не міг сказати, яка вкладка активна.
    setup();

    expect(screen.getByRole("tablist", { name: "Розділи" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(2);
  });

  it("позначає активну вкладку і зв'язує її з панеллю", () => {
    setup("analytics");

    const active = screen.getByRole("tab", { name: "Аналітика" });
    expect(active).toHaveAttribute("aria-selected", "true");
    expect(active).toHaveAttribute("aria-controls", "panel-analytics");

    expect(screen.getByRole("tab", { name: "Сьогодні" }))
      .toHaveAttribute("aria-selected", "false");
  });

  it("у табуляцію потрапляє лише активна вкладка", () => {
    // Roving tabindex: Tab заводить у набір і виводить із нього,
    // а всередині рухаються стрілками. Без цього довелося б
    // протискати табом кожну вкладку, щоб дістатися до вмісту.
    setup();

    expect(screen.getByRole("tab", { name: "Сьогодні" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Аналітика" })).toHaveAttribute("tabindex", "-1");
  });
});

describe("Tabs — клавіатура", () => {
  it("стрілка вправо переходить на наступну", async () => {
    const { onChange } = setup("today");

    screen.getByRole("tab", { name: "Сьогодні" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    expect(onChange).toHaveBeenCalledWith("analytics");
  });

  it("замикає список у кільце", async () => {
    // З останньої вкладки стрілка вправо веде на першу, а не в нікуди.
    const { onChange } = setup("analytics");

    screen.getByRole("tab", { name: "Аналітика" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    expect(onChange).toHaveBeenCalledWith("today");
  });

  it("стрілка вліво з першої веде на останню", async () => {
    // Тут ховалася б помилка зі знаком: у JavaScript -1 % 2 дорівнює
    // -1, а не 1, тож без додавання довжини індекс став би від'ємним.
    const { onChange } = setup("today");

    screen.getByRole("tab", { name: "Сьогодні" }).focus();
    await userEvent.keyboard("{ArrowLeft}");

    expect(onChange).toHaveBeenCalledWith("analytics");
  });

  it("Home веде на першу, End — на останню", async () => {
    const { onChange } = setup("analytics");
    screen.getByRole("tab", { name: "Аналітика" }).focus();

    await userEvent.keyboard("{Home}");
    expect(onChange).toHaveBeenCalledWith("today");

    await userEvent.keyboard("{End}");
    expect(onChange).toHaveBeenCalledWith("analytics");
  });

  it("клік теж працює", async () => {
    const { onChange } = setup("today");
    await userEvent.click(screen.getByRole("tab", { name: "Аналітика" }));
    expect(onChange).toHaveBeenCalledWith("analytics");
  });
});

describe("Tabs — іконки нижньої панелі", () => {
  it("іконка не змінює ім'я вкладки для диктора", () => {
    render(<Tabs items={[{ value: "today", label: "Сьогодні", icon: "✅" }]} value="today" onChange={() => {}} label="Розділи" />);
    // Якби емодзі потрапило в ім'я, диктор читав би «зелена галочка Сьогодні».
    expect(screen.getByRole("tab", { name: "Сьогодні" })).toBeInTheDocument();
  });
});
