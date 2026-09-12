import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useAllCheckins } from "../api/hooks";
import type { HabitWithStats } from "../api/types";
import { pluralDays } from "../lib/dates";
import { useChartColors } from "../lib/useChartColors";
import {
  dailySeries,
  habitSeries,
  summary,
  weekdaySeries,
  type HabitDays,
} from "./compute";

interface Props {
  items: HabitWithStats[];
  today: Date;
}

const PERIODS = [
  { days: 30, label: "30 днів" },
  { days: 90, label: "90 днів" },
];

/**
 * Екран аналітики.
 *
 * Три графіки відповідають на три різні питання, і кожен свідомо
 * показує ОДИН ряд даних:
 *
 *   «як я тримався останнім часом» — динаміка в часі;
 *   «яка звичка дається найгірше» — порівняння звичок;
 *   «коли я зриваюсь»              — розподіл по днях тижня.
 *
 * Спокуса намалювати по лінії на кожну звичку велика, але п'ять ліній
 * на одному полотні читаються гірше за один чесний підсумок: очі
 * шукають, яка лінія чия, замість того щоб побачити тенденцію.
 * З однією лінією не потрібна й легенда — заголовок уже все назвав.
 */
export function AnalyticsScreen({ items, today }: Props) {
  const [days, setDays] = useState(30);
  const colors = useChartColors();

  const habitIds = items.map((item) => item.habit.id);
  const { daysByHabit, isLoading } = useAllCheckins(habitIds, items.length > 0);

  if (items.length === 0) {
    return <p className="empty">Спершу додай хоч одну звичку — тоді буде що рахувати.</p>;
  }

  if (isLoading) {
    return (
      <div className="cards">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton" style={{ height: 88 }} />
        ))}
      </div>
    );
  }

  const habits: HabitDays[] = items.map((item) => ({
    id: item.habit.id,
    name: item.habit.name,
    days: daysByHabit.get(item.habit.id) ?? new Set<string>(),
  }));

  const totals = summary(habits, today, days);
  const daily = dailySeries(habits, today, days);
  const byHabit = habitSeries(habits, today, days);
  const byWeekday = weekdaySeries(habits, today, days);

  const bestStreak = Math.max(...items.map((item) => item.stats.longest_streak), 0);

  // «За весь час» береться зі статистики сервера, а не з завантажених
  // відміток: ті приходять лише за останнє вікно днів. Заразом це
  // дешевше — число вже пораховане в базі.
  const totalCheckins = items.reduce((sum, item) => sum + item.stats.total, 0);

  // Осі й сітка навмисно бліді: дані мають бути помітнішими за лінійку,
  // якою їх міряють.
  const axis = { stroke: colors.border, tick: { fill: colors.muted, fontSize: 11 } };

  return (
    <>
      {/*
        Фільтри — одним рядком над графіками, щоб було видно, до чого
        вони застосовуються.

        Тут `aria-pressed`, а не `aria-selected`, як у вкладках. Різниця
        не косметична: `aria-selected` дозволений лише на ролях кшталту
        `tab` чи `option` і на звичайній кнопці ігнорується. А це саме
        кнопки-перемикачі — вони не відкривають окремих панелей, а
        змінюють дані вже видимих графіків. Для такого стану в ARIA
        існує саме `aria-pressed`, і на кнопці він працює.
      */}
      <div className="tabs" role="group" aria-label="Період">
        {PERIODS.map((period) => (
          <button
            key={period.days}
            type="button"
            className="tab"
            aria-pressed={days === period.days}
            onClick={() => setDays(period.days)}
          >
            {period.label}
          </button>
        ))}
      </div>

      <div className="cards">
        <StatCard value={`${totals.rate}%`} label={`виконано за ${days} днів`} />
        <StatCard value={totalCheckins} label="відміток за весь час" />
        <StatCard value={bestStreak} label={`найдовша серія, ${pluralDays(bestStreak)}`} />
        <StatCard value={totals.activeDays} label={`активних днів із ${days}`} />
      </div>

      <section className="chart-card">
        <h2>Динаміка</h2>
        <p className="chart-note">Скільки звичок відмічено кожного дня періоду.</p>

        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={daily} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              {/*
                Заливка градієнтом від кольору лінії до прозорого.
                Суцільна площа такої висоти перетягувала б увагу на себе;
                згасання лишає акцент на верхній межі — саме вона й несе
                дані, площа під нею лише підказує напрямок.
              */}
              <linearGradient id="daily-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={colors.accent} stopOpacity={0.35} />
                <stop offset="100%" stopColor={colors.accent} stopOpacity={0} />
              </linearGradient>
            </defs>

            <CartesianGrid stroke={colors.border} vertical={false} />
            <XAxis
              dataKey="label"
              {...axis}
              // На 90 днях підписи злилися б у кашу. Показуємо кожен
              // сьомий — тобто приблизно по одному на тиждень.
              interval={Math.max(0, Math.floor(daily.length / 8))}
              tickLine={false}
            />
            <YAxis allowDecimals={false} {...axis} tickLine={false} width={40} />
            <Tooltip
              cursor={{ stroke: colors.muted, strokeWidth: 1 }}
              content={<DayTooltip />}
            />
            <Area
              type="monotone"
              dataKey="done"
              stroke={colors.accent}
              strokeWidth={2}
              fill="url(#daily-fill)"
              // Точка на кожен день перетворила б лінію на намисто.
              // Лишаємо їх лише під курсором.
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: colors.card }}
            />
          </AreaChart>
        </ResponsiveContainer>

        <DataTable
          caption="Динаміка по днях"
          head={["День", "Відмічено"]}
          rows={daily.map((point) => [point.label, String(point.done)])}
        />
      </section>

      <section className="chart-card">
        <h2>Звички поруч</h2>
        <p className="chart-note">Відсоток днів періоду, коли звичку виконано.</p>

        <ResponsiveContainer width="100%" height={Math.max(120, byHabit.length * 42)}>
          <BarChart
            data={byHabit}
            layout="vertical"
            margin={{ top: 0, right: 32, bottom: 0, left: 0 }}
          >
            <CartesianGrid stroke={colors.border} horizontal={false} />
            <XAxis type="number" domain={[0, 100]} unit="%" {...axis} tickLine={false} />
            <YAxis
              type="category"
              dataKey="name"
              width={110}
              {...axis}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip cursor={{ fill: colors.border, fillOpacity: 0.35 }} content={<HabitTooltip />} />
            {/* radius округлює саме той край, де стовпчик закінчується:
                біля осі він має лишатися прямим, інакше «відклеюється». */}
            <Bar dataKey="percent" fill={colors.accent} radius={[0, 4, 4, 0]} barSize={18} />
          </BarChart>
        </ResponsiveContainer>

        <DataTable
          caption="Звички за період"
          head={["Звичка", "Виконано", "Відсоток"]}
          rows={byHabit.map((point) => [point.name, String(point.done), `${point.percent}%`])}
        />
      </section>

      <section className="chart-card">
        <h2>Дні тижня</h2>
        <p className="chart-note">
          Де провал — там і варто шукати причину: відсоток рахується від можливого,
          тому зайвий понеділок у періоді результат не спотворює.
        </p>

        <ResponsiveContainer width="100%" height={200}>
          {/*
            Від'ємного лівого відступу тут бути не може: підписи осі —
            "100%", а не "4", і вони на ньому обрізаються до ")%".
            Ширину задаємо явно, з запасом під найдовший підпис.
          */}
          <BarChart data={byWeekday} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={colors.border} vertical={false} />
            <XAxis dataKey="label" {...axis} tickLine={false} />
            <YAxis domain={[0, 100]} unit="%" {...axis} tickLine={false} width={48} />
            <Tooltip cursor={{ fill: colors.border, fillOpacity: 0.35 }} content={<WeekdayTooltip />} />
            <Bar dataKey="percent" fill={colors.accent} radius={[4, 4, 0, 0]} maxBarSize={40} />
          </BarChart>
        </ResponsiveContainer>

        <DataTable
          caption="Дні тижня"
          head={["День", "Відсоток"]}
          rows={byWeekday.map((point) => [point.label, `${point.percent}%`])}
        />
      </section>
    </>
  );
}

function StatCard({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="stat-card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

/**
 * Підказки під курсором.
 *
 * Recharts має власну, але вона намальована вбудованими стилями й
 * лишається білою в темній темі. Своя бере кольори з тих самих
 * CSS-змінних, що й решта сторінки.
 */
interface TooltipProps {
  active?: boolean;
  payload?: Array<{ payload?: unknown }>;
}

function firstPayload<T>(props: TooltipProps): T | null {
  if (!props.active || !props.payload || props.payload.length === 0) return null;
  return (props.payload[0]?.payload ?? null) as T | null;
}

function DayTooltip(props: TooltipProps) {
  const point = firstPayload<{ label: string; done: number; total: number }>(props);
  if (!point) return null;

  return (
    <div className="chart-tooltip">
      <strong>{point.label}</strong>
      {point.done} з {point.total}
    </div>
  );
}

function HabitTooltip(props: TooltipProps) {
  const point = firstPayload<{ name: string; percent: number; done: number }>(props);
  if (!point) return null;

  return (
    <div className="chart-tooltip">
      <strong>{point.name}</strong>
      {point.percent}% — {point.done} {pluralDays(point.done)}
    </div>
  );
}

function WeekdayTooltip(props: TooltipProps) {
  const point = firstPayload<{ label: string; percent: number }>(props);
  if (!point) return null;

  return (
    <div className="chart-tooltip">
      <strong>{point.label}</strong>
      {point.percent}% виконання
    </div>
  );
}

/**
 * Ті самі дані таблицею.
 *
 * Графік недоступний тому, хто користується екранним диктором, і
 * незручний тому, кому треба точне число, а не «десь отут». Таблиця
 * під кожним графіком закриває обидва випадки, а <details> лишає її
 * згорнутою, щоб не заважати решті.
 */
function DataTable({
  caption,
  head,
  rows,
}: {
  caption: string;
  head: string[];
  rows: string[][];
}) {
  return (
    <details>
      <summary className="chart-note" style={{ cursor: "pointer" }}>
        Показати числа
      </summary>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {head.map((cell) => (
              <th key={cell} style={{ textAlign: "left", padding: "0.25rem 0.5rem" }}>
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, index) => (
                <td key={index} style={{ padding: "0.25rem 0.5rem" }}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
