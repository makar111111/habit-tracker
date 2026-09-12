import {
  CALENDAR_WEEKS,
  MONTH_NAMES,
  WEEKDAY_LABELS,
  addDays,
  gridStart,
  toISO,
} from "../lib/dates";

interface Props {
  /** Відмічені дні у форматі `YYYY-MM-DD`. */
  doneDays: Set<string>;
  today: Date;
  weeks?: number;
}

/**
 * Теплова карта за останні тижні — та сама, що на профілі GitHub.
 *
 * Чому саме такий вигляд: серія важлива не числом, а формою. Суцільна
 * смуга й дірка посеред неї помітні миттєво, тоді як «поточна серія:
 * 7» доводиться читати й порівнювати з учорашнім значенням у голові.
 *
 * Порівняння дат зроблено на РЯДКАХ, а не на об'єктах Date. Формат
 * `YYYY-MM-DD` має зручну властивість: він упорядкований лексикографічно
 * так само, як хронологічно. Тому `iso > todayIso` — це коректна
 * перевірка «в майбутньому», і при цьому вона не створює сотні
 * об'єктів Date і не залежить від годинного поясу.
 */
export function Calendar({ doneDays, today, weeks = CALENDAR_WEEKS }: Props) {
  const start = gridStart(today, weeks);
  const todayIso = toISO(today);

  // Підпис місяця ставимо лише над тим тижнем, де місяць змінився —
  // інакше "вер вер вер вер" займало б увесь рядок без користі.
  let previousMonth = -1;
  const monthLabels = Array.from({ length: weeks }, (_, week) => {
    const monday = addDays(start, week * 7);
    const month = monday.getMonth();
    const label = month === previousMonth ? "" : MONTH_NAMES[month];
    previousMonth = month;
    return label;
  });

  const cells = Array.from({ length: weeks * 7 }, (_, index) => {
    const iso = toISO(addDays(start, index));
    const isFuture = iso > todayIso;
    const isDone = doneDays.has(iso);

    const classes = ["day"];
    if (isFuture) classes.push("future");
    else if (isDone) classes.push("done");
    if (iso === todayIso) classes.push("today");

    return (
      <div
        key={iso}
        className={classes.join(" ")}
        title={isFuture ? undefined : `${iso} — ${isDone ? "зроблено" : "пропуск"}`}
      />
    );
  });

  return (
    <div className="calendar">
      {/*
        Підписи місяців лежать УСЕРЕДИНІ правої колонки, поряд із
        сіткою, а не окремим рядком над усім блоком. Інакше вони
        починалися б від лівого краю — тобто були б зсунуті на ширину
        стовпчика з «Пн/Ср/Пт» і стояли б не над своїми тижнями.
      */}
      <div className="calendar-body">
        <div className="calendar-labels" aria-hidden="true">
          {WEEKDAY_LABELS.map((label, index) => (
            <div key={index}>{label}</div>
          ))}
        </div>

        <div>
          <div className="calendar-months" aria-hidden="true">
            {monthLabels.map((label, index) => (
              // Ширина стовпця (12px) плюс проміжок (3px) — підпис має
              // стояти рівно над своїм тижнем.
              <span key={index} style={{ width: 15 }}>
                {label}
              </span>
            ))}
          </div>

          {/*
            Сітка для екранного диктора — суцільний шум: 84 порожні
            комірки. Ховаємо її й даємо замість неї одне речення підсумку
            нижче. Це не спрощення заради ліні: діаграма, яку не можна
            побачити, найкраще передається словами.
          */}
          <div className="calendar-grid" aria-hidden="true">
            {cells}
          </div>
        </div>
      </div>

      <p className="visually-hidden">
        За останні {weeks * 7} днів відмічено {countVisible(doneDays, start, today)} днів.
      </p>

      <div className="calendar-legend" aria-hidden="true">
        <span>пропуск</span>
        <span className="day" />
        <span className="day done" />
        <span>зроблено</span>
      </div>
    </div>
  );
}

function countVisible(doneDays: Set<string>, start: Date, today: Date): number {
  const startIso = toISO(start);
  const todayIso = toISO(today);
  let count = 0;
  for (const day of doneDays) {
    if (day >= startIso && day <= todayIso) count += 1;
  }
  return count;
}
