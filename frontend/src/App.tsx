import { Suspense, lazy, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { isUnauthorized, markLoggedIn, useLogout, useMe, useToday } from "./api/hooks";
import type { User } from "./api/types";
import { clearPrivateQueries } from "./api/queryClient";
import { ArchiveScreen } from "./components/ArchiveScreen";
import { ErrorBanner } from "./components/ErrorBanner";
import { LoginScreen } from "./components/LoginScreen";
import { QueryError } from "./components/QueryError";
import { SettingsScreen } from "./components/SettingsScreen";
import { Tabs } from "./components/Tabs";
import { ThemeToggle } from "./components/ThemeToggle";
import { TodayScreen } from "./components/TodayScreen";
import { formatShort, fromISO } from "./lib/dates";

// Графіки завантажуються лише після відкриття аналітики.
const AnalyticsScreen = lazy(() => import("./analytics/AnalyticsScreen").then((module) => ({ default: module.AnalyticsScreen })));
const TABS = [
  { value: "today" as const, label: "Сьогодні" },
  { value: "analytics" as const, label: "Аналітика" },
  { value: "archive" as const, label: "Архів" },
  { value: "settings" as const, label: "Налаштування" },
];
type Tab = typeof TABS[number]["value"];

export function App() {
  const client = useQueryClient();
  const me = useMe();
  useEffect(() => { if (me.data === null) clearPrivateQueries(client); }, [client, me.data]);
  if (me.isPending) return <main className="shell" aria-busy="true"><p className="chart-note">Завантажую профіль…</p></main>;
  if (me.isError && !isUnauthorized(me.error)) return <main className="shell"><QueryError error={me.error} retry={me.refetch} busy={me.isFetching} /></main>;
  if (!me.data) return <LoginScreen onSuccess={() => markLoggedIn(client)} />;
  return <Dashboard key={me.data.id} user={me.data} />;
}

function Dashboard({ user }: { user: User }) {
  const logout = useLogout();
  const day = useToday(true);
  const [tab, setTab] = useState<Tab>("today");
  const today = day.data ? fromISO(day.data.day) : null;
  return <main className="shell">
    <div className="top"><h1>Трекер звичок</h1><ThemeToggle /></div>
    <p className="subline">
      <span>{today ? `Сьогодні: ${formatShort(today)}` : "Визначаю дату…"}</span>
      <span aria-hidden="true">·</span><span>{user.name || "без імені"}</span>
      <span aria-hidden="true">·</span>
      <button type="button" className="link-button" disabled={logout.isPending} onClick={() => logout.mutate()}>вийти</button>
    </p>
    <Tabs items={TABS} value={tab} onChange={setTab} label="Розділи" />
    <ErrorBanner />
    <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}>
      {tab === "settings" ? <SettingsScreen user={user} />
        : day.isError ? <QueryError error={day.error} retry={day.refetch} busy={day.isFetching} />
        : !today ? <div className="skeleton" aria-label="Завантажую дату" />
        : tab === "today" ? <TodayScreen today={today} />
        : tab === "archive" ? <ArchiveScreen today={today} />
        : <Suspense fallback={<div className="skeleton" aria-label="Завантажую аналітику" />}><AnalyticsScreen today={today} /></Suspense>}
    </div>
  </main>;
}
