import { CALENDAR_WEEKS, MONTH_NAMES, addDays, gridStart, toISO } from "../lib/dates";
import { WEEKDAYS, isScheduledOn, type Schedule } from "../lib/schedule";

interface Props {
  doneDays: Set<string>;
  today: Date;
  end?: Date;
  weeks?: number;
  habit?: Schedule;
  disabled?: boolean;
  onToggle: (day: string, done: boolean) => void;
}

/** Кожна клітинка має доступну дату, стан і дію; майбутні дні не редагуються. */
export function Calendar({ doneDays, today, end = today, weeks = CALENDAR_WEEKS,
  habit = {}, disabled = false, onToggle }: Props) {
  const start = gridStart(end, weeks);
  const todayIso = toISO(today);
  let previousMonth = -1;
  const monthLabels = Array.from({ length: weeks }, (_, week) => {
    const monday = addDays(start, week * 7);
    const month = monday.getMonth();
    const label = month === previousMonth ? "" : MONTH_NAMES[month];
    previousMonth = month;
    return label;
  });

  return (
    <div className="calendar">
      <div className="calendar-body">
        <div className="calendar-labels" aria-hidden="true">
          {WEEKDAYS.map((label) => <div key={label}>{label}</div>)}
        </div>
        <div>
          <div className="calendar-months" aria-hidden="true">
            {monthLabels.map((label, index) => <span key={index}>{label}</span>)}
          </div>
          <div className="calendar-grid" role="group" aria-label="Відмітки за датами">
            {Array.from({ length: weeks * 7 }, (_, index) => {
              const day = toISO(addDays(start, index));
              const future = day > todayIso;
              const afterArchive = Boolean(habit.archived_at && day > habit.archived_at);
              const beforeStart = Boolean(habit.start_date && day < habit.start_date);
              const done = doneDays.has(day);
              const planned = isScheduledOn(habit, day);
              const state = future ? "майбутній день" : done ? "зроблено" : afterArchive ? "після архівування"
                : beforeStart ? "до початку" : planned ? "пропуск" : "поза розкладом";
              // Архів забороняє нові дні, але не приховує помилкову стару
              // відмітку: сервер і надалі дозволяє її прибрати.
              const blocked = disabled || future || (afterArchive && !done);
              const label = `${day} — ${state}${blocked ? "" : done ? "; зняти відмітку" : "; відмітити"}`;
              return <button key={day} type="button"
                className={`day day-button${done ? " done" : ""}${future || afterArchive ? " future" : ""}${!planned ? " rest" : ""}${day === todayIso ? " today" : ""}`}
                aria-label={label} title={label} aria-pressed={done} disabled={blocked}
                onClick={() => onToggle(day, done)}><span aria-hidden="true">{done ? "✓" : ""}</span></button>;
            })}
          </div>
        </div>
      </div>
      <div className="calendar-legend" aria-hidden="true">
        <span className="day" /><span>заплановано</span>
        <span className="day rest" /><span>поза розкладом</span>
        <span className="day done" /><span>зроблено</span>
      </div>
      <p className="chart-note calendar-note">Натисни день, щоб поставити або зняти відмітку. Відмітка до початку посуне дату початку звички.</p>
    </div>
  );
}
