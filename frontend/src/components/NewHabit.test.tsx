import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../api/queryClient";
import { NewHabit } from "./NewHabit";

const TODAY = new Date(2026, 8, 13);

function renderNewHabit(empty: boolean) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <NewHabit today={TODAY} empty={empty} />
    </QueryClientProvider>,
  );
}

describe("NewHabit", () => {
  it("коли звички є, згорнута в одну кнопку", () => {
    // Раніше форма займала на телефоні весь перший екран.
    renderNewHabit(false);
    expect(screen.getByRole("button", { name: "Нова звичка" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByRole("textbox", { name: "Назва звички" })).not.toBeInTheDocument();
  });

  it("розгортається й одразу ставить фокус на назву", async () => {
    renderNewHabit(false);
    await userEvent.click(screen.getByRole("button", { name: "Нова звичка" }));
    expect(screen.getByRole("button", { name: "Нова звичка" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("textbox", { name: "Назва звички" })).toHaveFocus();
  });

  it("Escape згортає форму й повертає фокус на кнопку", async () => {
    renderNewHabit(false);
    await userEvent.click(screen.getByRole("button", { name: "Нова звичка" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: "Назва звички" })).not.toBeInTheDocument();
    // Без цього фокус упав би на <body>, і з клавіатури довелося б іти від верху сторінки.
    expect(screen.getByRole("button", { name: "Нова звичка" })).toHaveFocus();
  });

  it("«Скасувати» теж згортає", async () => {
    renderNewHabit(false);
    await userEvent.click(screen.getByRole("button", { name: "Нова звичка" }));
    await userEvent.click(screen.getByRole("button", { name: "Скасувати" }));
    expect(screen.queryByRole("textbox", { name: "Назва звички" })).not.toBeInTheDocument();
  });

  it("без звичок форма відкрита одразу й без «Скасувати»", () => {
    // На порожньому екрані створення — єдина корисна дія, згортати її нема сенсу.
    renderNewHabit(true);
    expect(screen.getByRole("textbox", { name: "Назва звички" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Нова звичка" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Скасувати" })).not.toBeInTheDocument();
  });
});
