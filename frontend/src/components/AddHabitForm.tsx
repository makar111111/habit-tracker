import { useState } from "react";

import { useCreateHabit } from "../api/hooks";
import { toISO } from "../lib/dates";
import { EVERY_DAY, WEEKDAYS } from "../lib/schedule";

export function AddHabitForm({ today }: { today: Date }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [weekdays, setWeekdays] = useState(EVERY_DAY);
  const startDate = selectedDate ?? toISO(today);
  const create = useCreateHabit();

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (create.isPending || !name.trim() || weekdays.length === 0 || !startDate || startDate > toISO(today)) return;
    create.mutate({ name: name.trim(), description: description.trim(), start_date: startDate, weekdays }, {
      onSuccess: () => { setName(""); setDescription(""); setSelectedDate(null); setWeekdays(EVERY_DAY); },
    });
  }

  return <form className="add-form" onSubmit={submit}>
    <div className="add-form-main">
      <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Назва звички" maxLength={100} required aria-label="Назва звички" />
      <input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Опис (необов'язково)" aria-label="Опис звички" />
      <button type="submit" className="primary" disabled={create.isPending || !name.trim() || weekdays.length === 0}>
        {create.isPending ? "Додаю…" : "Додати"}
      </button>
    </div>
    <div className="schedule-fields">
      <label className="field">Дата початку
        <input type="date" value={startDate} max={toISO(today)} required onChange={(event) => setSelectedDate(event.target.value)} />
      </label>
      <fieldset className="weekdays"><legend>Дні виконання</legend>
        <div className="weekday-options">{WEEKDAYS.map((label, day) => <label key={label}>
          <input type="checkbox" checked={weekdays.includes(day)} onChange={() => setWeekdays((current) =>
            current.includes(day) ? current.filter((value) => value !== day) : [...current, day].sort((a, b) => a - b))} />
          <span>{label}</span>
        </label>)}</div>
      </fieldset>
    </div>
    <p className="chart-note">Обери хоча б один день. Розклад зберігається при створенні, щоб історія лишалася точною.</p>
  </form>;
}
