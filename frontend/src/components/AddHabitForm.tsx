import { useState } from "react";

import { useCreateHabit } from "../api/hooks";

/**
 * Форма створення звички.
 *
 * Поля тут «керовані» (controlled): значення живе у стані React, а
 * input лише його відображає. Альтернатива — читати значення з DOM
 * при відправці — коротша, але тоді кнопку не можна вимкнути, поки
 * назва порожня: React просто не знає, що там набрали.
 */
export function AddHabitForm() {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useCreateHabit();

  function submit(event: React.FormEvent) {
    event.preventDefault();

    const trimmed = name.trim();
    if (!trimmed) return;

    create.mutate(
      { name: trimmed, description: description.trim() },
      {
        // Чистимо поля лише після успіху. Якщо сервер відмовив,
        // набраний текст лишається на місці — переписувати його
        // заново через чужу помилку було б знущанням.
        onSuccess: () => {
          setName("");
          setDescription("");
        },
      },
    );
  }

  return (
    <form className="add-form" onSubmit={submit}>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Назва звички"
        maxLength={100}
        required
        aria-label="Назва звички"
      />
      <input
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Опис (необов'язково)"
        aria-label="Опис звички"
      />
      <button type="submit" className="primary" disabled={create.isPending || !name.trim()}>
        {create.isPending ? "Додаю…" : "Додати"}
      </button>
    </form>
  );
}
