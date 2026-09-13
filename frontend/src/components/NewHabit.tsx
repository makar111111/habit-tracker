import { useRef, useState } from "react";

import { AddHabitForm } from "./AddHabitForm";

const FORM_ID = "new-habit-form";

/**
 * Створення звички, згорнуте в одну кнопку.
 *
 * Звичку створюють раз на тиждень, а відмічають щодня. Розгорнута форма
 * займала на телефоні весь перший екран, і перша звичка з'являлася лише
 * на ~850-му пікселі. Поки звичок немає, форма розгорнута одразу: іншої
 * корисної дії на порожньому екрані просто немає.
 */
export function NewHabit({ today, empty }: { today: Date; empty: boolean }) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);

  // Фокус повертається на кнопку, а не губиться на <body>: інакше після
  // Escape людина з клавіатурою мусила б табом іти від самого верху сторінки.
  function close() {
    setOpen(false);
    toggle.current?.focus();
  }

  if (empty) return <AddHabitForm today={today} />;

  return <>
    <button ref={toggle} type="button" className="new-habit-toggle" aria-expanded={open} aria-controls={open ? FORM_ID : undefined}
      onClick={() => (open ? close() : setOpen(true))}>
      <span aria-hidden="true">{open ? "−" : "+"}</span> Нова звичка
    </button>
    {open && <AddHabitForm today={today} id={FORM_ID} autoFocus onCreated={close} onCancel={close} />}
  </>;
}
