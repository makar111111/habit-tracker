import { Suspense, lazy, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { isUnauthorized, markLoggedIn, useHabitsWithStats, useLogout, useMe } from "./api/hooks";
import { AddHabitForm } from "./components/AddHabitForm";
import { HabitList } from "./components/HabitList";
import { LoginScreen } from "./components/LoginScreen";
import { ProgressPanel } from "./components/ProgressPanel";
import { ThemeToggle } from "./components/ThemeToggle";
import { formatShort } from "./lib/dates";

/**
 * Аналітика вантажиться окремим шматком і лише за потреби.
 *
 * Причина груба й вимірювана: бібліотека графіків займає більшу
 * частину бандла. Якщо покласти її в основний файл, кожен, хто просто
 * зайшов поставити галочку, платив би завантаженням коду, якого не
 * відкриє. `lazy` перетворює цей імпорт на окремий запит, що йде лише
 * при переході на вкладку.
 *
 * Тут потрібен саме `.then` з перейменуванням: React.lazy чекає модуль
 * із полем `default`, а в проєкті домовлено про іменовані експорти.
 * Ця обгортка — місток між двома домовленостями.
 */
const AnalyticsScreen = lazy(() =>
  import("./analytics/AnalyticsScreen").then((module) => ({
    default: module.AnalyticsScreen,
  })),
);

type Tab = "today" | "analytics";

export function App() {
  const client = useQueryClient();
  const me = useMe();
  const logout = useLogout();
  const [tab, setTab] = useState<Tab>("today");

  // Дату беремо один раз на рендер і передаємо вниз явним параметром.
  // Якби кожен компонент викликав new Date() сам, вони могли б
  // розійтися — рідко, але рівно опівночі, і саме тоді, коли людина
  // ставить останню за день відмітку.
  const today = new Date();

  const authenticated = me.isSuccess;
  const { items, isLoading, error } = useHabitsWithStats(authenticated);

  if (me.isLoading) {
    // Порожній екран замість блимання формою входу: поки ми не знаємо,
    // чи є сесія, показувати «увійди» — значить лякати людину, яка
    // насправді вже увійшла.
    return <main className="shell" aria-busy="true" />;
  }

  if (me.isError && isUnauthorized(me.error)) {
    return <LoginScreen onSuccess={() => markLoggedIn(client)} />;
  }

  if (me.isError) {
    // Сервер лежить або впав із 500 — це вже не про автентифікацію,
    // і пропонувати вхід тут було б обманом.
    return (
      <main className="shell">
        <p className="error">{me.error.message}</p>
      </main>
    );
  }

  return (
    <main className="shell">
      <div className="top">
        <h1>Трекер звичок</h1>
        <ThemeToggle />
      </div>

      <p className="subline">
        <span>Сьогодні: {formatShort(today)}</span>
        <span aria-hidden="true">·</span>
        <span>{me.data?.name || "без імені"}</span>
        <span aria-hidden="true">·</span>
        <button type="button" className="link-button" onClick={() => logout.mutate()}>
          вийти
        </button>
      </p>

      {/* role="tablist" без повного набору ARIA був би гіршим за його
          відсутність, тому тут проста група кнопок із aria-selected. */}
      <div className="tabs" role="group" aria-label="Розділи">
        <button
          type="button"
          className="tab"
          aria-selected={tab === "today"}
          onClick={() => setTab("today")}
        >
          Сьогодні
        </button>
        <button
          type="button"
          className="tab"
          aria-selected={tab === "analytics"}
          onClick={() => setTab("analytics")}
        >
          Аналітика
        </button>
      </div>

      {error && <p className="error">{error.message}</p>}

      {tab === "today" ? (
        <>
          <ProgressPanel items={items} />
          <AddHabitForm />
          <HabitList items={items} isLoading={isLoading} today={today} />
        </>
      ) : (
        // Suspense показує запасний вміст, поки шматок із графіками
        // летить по мережі. Без нього React кинув би помилку: він не
        // має права малювати компонент, якого ще немає.
        <Suspense fallback={<div className="skeleton" style={{ height: 200 }} />}>
          <AnalyticsScreen items={items} today={today} />
        </Suspense>
      )}
    </main>
  );
}
