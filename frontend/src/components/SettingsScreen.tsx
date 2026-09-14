import { useState } from "react";

import { useUpdateMe } from "../api/hooks";
import type { User } from "../api/types";

export function SettingsScreen({ user }: { user: User }) {
  const [name, setName] = useState(user.name);
  const [timezone, setTimezone] = useState(user.timezone);
  const [hour, setHour] = useState(user.reminder_hour);
  const [enabled, setEnabled] = useState(user.reminders_enabled);
  const save = useUpdateMe();
  const nameChanged = name !== user.name;
  const invalidName = nameChanged && !name.trim();
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones = [
    ...new Set([
      user.timezone,
      browserZone,
      "Europe/Kyiv",
      "UTC",
      "Europe/London",
      "America/New_York",
    ]),
  ];

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (save.isPending || invalidName || !timezone.trim()) return;
    save.mutate({
      ...(nameChanged ? { name: name.trim() } : {}),
      timezone: timezone.trim(),
      reminder_hour: hour,
      reminders_enabled: enabled,
    });
  }

  return (
    <section className="settings chart-card">
      <h2>Профіль і нагадування</h2>
      <form onSubmit={submit} onChange={() => save.reset()}>
        <fieldset disabled={save.isPending} className="settings-fields">
          <label className="field">
            Ім’я
            <input
              value={name}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
              autoComplete="name"
              aria-invalid={invalidName || undefined}
              aria-describedby={invalidName ? "name-error" : undefined}
            />
          </label>
          {invalidName && (
            <p className="chart-note" id="name-error">
              Ім’я не може бути порожнім.
            </p>
          )}
          <label className="field">
            Часовий пояс
            <input
              list="timezones"
              value={timezone}
              required
              onChange={(event) => setTimezone(event.target.value)}
              aria-describedby="timezone-help"
            />
            <datalist id="timezones">
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </label>
          <p className="chart-note" id="timezone-help">
            Визначає сьогоднішню дату й час нагадування. Наприклад, Europe/Kyiv.
          </p>
          <button
            type="button"
            className="link-button browser-zone"
            onClick={() => {
              setTimezone(browserZone);
              save.reset();
            }}
          >
            Використати пояс браузера: {browserZone}
          </button>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            Нагадування в Telegram
          </label>
          <label className="field">
            Година нагадування
            <select
              value={hour}
              disabled={!enabled}
              onChange={(event) => setHour(Number(event.target.value))}
            >
              {Array.from({ length: 24 }, (_, value) => (
                <option key={value} value={value}>
                  {String(value).padStart(2, "0")}:00
                </option>
              ))}
            </select>
          </label>
          {user.telegram_id === null && (
            <p className="chart-note">Для нагадувань увійди через Telegram.</p>
          )}
        </fieldset>
        <button
          type="submit"
          className="primary"
          disabled={save.isPending || invalidName || !timezone.trim()}
        >
          {save.isPending ? "Зберігаю…" : "Зберегти налаштування"}
        </button>
        {save.isSuccess && (
          <p className="save-feedback" role="status">
            Налаштування збережено.
          </p>
        )}
      </form>
      <div className="export-data">
        <h2>Копія даних</h2>
        <p className="chart-note">Усі звички, архів і відмітки в одному файлі.</p>
        <a className="link-button" href="/users/me/export" download>
          Завантажити мої дані (JSON)
        </a>
      </div>
    </section>
  );
}
