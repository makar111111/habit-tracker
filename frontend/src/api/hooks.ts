import { useEffect, useRef } from "react";
import {
  useIsMutating,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import * as api from "./client";
import { isUnauthorized } from "./client";
import { keys } from "./keys";
import { endSession, sessionVersion } from "./queryClient";
import { addDays, toISO } from "../lib/dates";
import type {
  Checkin,
  CreateHabitInput,
  Habit,
  HabitStats,
  HabitWithStats,
  User,
  UserChanges,
} from "./types";

// Аналітика й календар ділять одне 120-денне вікно. Повні підсумки бере /stats.
export const CHECKIN_WINDOW_DAYS = 120;
const HABIT_CHANGE = keys.habitChange;

function windowStart(end: Date): string {
  return toISO(addDays(end, -(CHECKIN_WINDOW_DAYS - 1)));
}

export function useMe() {
  return useQuery<User | null>({
    queryKey: keys.me,
    queryFn: api.getMe,
    retry: false,
    staleTime: Infinity,
  });
}

/** Дата належить часовому поясу профілю. Перевіряємо щохвилини і при поверненні. */
export function useToday(enabled: boolean) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.today,
    queryFn: api.getToday,
    enabled,
    staleTime: 0,
    refetchInterval: () => 60_000 - (Date.now() % 60_000),
    refetchOnWindowFocus: "always",
  });
  const previous = useRef(query.data?.day);
  useEffect(() => {
    if (previous.current && query.data?.day && previous.current !== query.data.day) {
      void client.invalidateQueries({ queryKey: keys.stats });
    }
    previous.current = query.data?.day;
  }, [client, query.data?.day]);
  return query;
}

export function useHabits(enabled: boolean, includeArchived = false) {
  return useQuery<Habit[]>({
    queryKey: includeArchived ? keys.allHabits : keys.habits,
    queryFn: () => api.listHabits(includeArchived),
    enabled,
  });
}

export function useStats(enabled: boolean, includeArchived = false) {
  return useQuery<HabitStats[]>({
    queryKey: includeArchived ? keys.allStats : keys.stats,
    queryFn: () => api.listStats(includeArchived),
    enabled,
  });
}

export function useHabitsWithStats(enabled: boolean, includeArchived = false) {
  const habits = useHabits(enabled, includeArchived);
  const stats = useStats(enabled, includeArchived);
  const byId = new Map((stats.data ?? []).map((row) => [row.habit_id, row]));
  const items: HabitWithStats[] = (habits.data ?? []).map((habit) => ({
    habit,
    stats: byId.get(habit.id) ?? emptyStats(habit.id),
  }));
  return {
    items,
    isLoading: habits.isLoading || stats.isLoading,
    error: habits.error ?? stats.error,
    refetch: () => Promise.all([habits.refetch(), stats.refetch()]),
  };
}

function emptyStats(habitId: number): HabitStats {
  return {
    habit_id: habitId,
    total: 0,
    current_streak: 0,
    longest_streak: 0,
    done_today: false,
    last_day: null,
  };
}

export function useCheckins(habitId: number, enabled: boolean, end: Date) {
  const since = windowStart(end);
  return useQuery({
    queryKey: keys.checkins(habitId, since),
    queryFn: () => api.listCheckins(habitId, { since, until: toISO(end) }),
    enabled,
  });
}

export function useAllCheckins(habitIds: number[], enabled: boolean, today: Date) {
  const since = windowStart(today);
  const results = useQueries({
    queries: habitIds.map((id) => ({
      queryKey: keys.checkins(id, since),
      queryFn: () => api.listCheckins(id, { since, until: toISO(today) }),
      enabled,
    })),
  });
  const daysByHabit = new Map<number, Set<string>>();
  habitIds.forEach((id, index) => {
    const data = results[index]?.data;
    if (data) daysByHabit.set(id, new Set(data.map((checkin) => checkin.day)));
  });
  return {
    daysByHabit,
    isLoading: results.some((result) => result.isLoading),
    error: results.find((result) => result.error)?.error ?? null,
    refetch: () => Promise.all(results.map((result) => result.refetch())),
  };
}

export function useHabitPending(habitId: number): boolean {
  return (
    useIsMutating({
      mutationKey: HABIT_CHANGE,
      predicate: (mutation) => {
        const variables = mutation.state.variables;
        return typeof variables === "number"
          ? variables === habitId
          : (variables as { habitId?: number } | undefined)?.habitId === habitId;
      },
    }) > 0
  );
}

interface ToggleInput {
  habitId: number;
  done: boolean;
  day: string;
  today: string;
}

export function useToggleCheckin() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: HABIT_CHANGE,
    mutationFn: ({ habitId, done, day }: ToggleInput) =>
      done ? api.undoCheckIn(habitId, day) : api.checkIn(habitId, day),
    onMutate: async ({ habitId, done, day, today }) => {
      await Promise.all([
        client.cancelQueries({ queryKey: keys.stats }),
        client.cancelQueries({ queryKey: keys.checkinsOf(habitId) }),
      ]);
      // Зберігаємо тільки рядок цієї звички: її помилка не скасує сусідню відмітку.
      const snapshots = client
        .getQueriesData<HabitStats[]>({ queryKey: keys.stats })
        .map(([key, rows]) => ({
          key,
          row: rows?.find((row) => row.habit_id === habitId),
        }));
      client.setQueriesData<HabitStats[]>({ queryKey: keys.stats }, (old) =>
        old?.map((row) => {
          if (row.habit_id !== habitId) return row;
          const changed = applyToggle(row, done, day);
          return day === today ? changed : { ...changed, done_today: row.done_today };
        }),
      );
      const history = client.getQueriesData<Checkin[]>({ queryKey: keys.checkinsOf(habitId) });
      for (const [key, rows] of history) {
        // Вікно задається since й має фіксовану довжину. Не домішуємо чужий період.
        const since = String(key[2]);
        if (
          !rows ||
          day < since ||
          day > toISO(addDays(new Date(`${since}T00:00:00`), CHECKIN_WINDOW_DAYS - 1))
        )
          continue;
        client.setQueryData<Checkin[]>(
          key,
          done
            ? rows.filter((row) => row.day !== day)
            : [...rows.filter((row) => row.day !== day), { id: -1, habit_id: habitId, day }],
        );
      }
      return { snapshots, history };
    },
    onError: (error, { habitId }, context) => {
      if (isUnauthorized(error)) return;
      for (const { key, row } of context?.snapshots ?? []) {
        if (row)
          client.setQueryData<HabitStats[]>(key, (current) =>
            current?.map((item) => (item.habit_id === habitId ? row : item)),
          );
      }
      for (const [key, rows] of context?.history ?? []) client.setQueryData(key, rows);
    },
    onSettled: async (_data, error, { habitId }) => {
      if (isUnauthorized(error)) return;
      await client.invalidateQueries({ queryKey: keys.checkinsOf(habitId) });
    },
  });
}

/** Серії залежать від розкладу та історії; їхній точний результат поверне сервер. */
export function applyToggle(stats: HabitStats, wasDone: boolean, day?: string): HabitStats {
  return {
    ...stats,
    done_today: !wasDone,
    total: Math.max(0, stats.total + (wasDone ? -1 : 1)),
    last_day: !wasDone && day && (!stats.last_day || day > stats.last_day) ? day : stats.last_day,
  };
}

export function useCreateHabit() {
  return useMutation({
    mutationKey: HABIT_CHANGE,
    mutationFn: (input: CreateHabitInput) => api.createHabit(input),
  });
}

export function useDeleteHabit() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: HABIT_CHANGE,
    mutationFn: api.deleteHabit,
    onSuccess: (_data, habitId) =>
      client.removeQueries({ queryKey: keys.checkinsOf(habitId), type: "inactive" }),
  });
}

export function useRenameHabit() {
  return useMutation({
    mutationKey: HABIT_CHANGE,
    mutationFn: ({ habitId, name }: { habitId: number; name: string }) =>
      api.updateHabit(habitId, { name }),
  });
}

export function useArchiveHabit() {
  return useMutation({
    mutationKey: HABIT_CHANGE,
    mutationFn: ({ habitId, archived }: { habitId: number; archived: boolean }) =>
      api.updateHabit(habitId, { archived }),
  });
}

export function useUpdateMe() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (changes: UserChanges) => api.updateMe(changes),
    onMutate: () => sessionVersion(client),
    onSuccess: async (user, _changes, version) => {
      if (version !== sessionVersion(client)) return;
      client.setQueryData(keys.me, user);
      await client.invalidateQueries({ queryKey: keys.today });
      await client.invalidateQueries({ queryKey: keys.stats });
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({ mutationFn: api.logout, onSuccess: () => endSession(client) });
}

export function markLoggedIn(client: QueryClient): void {
  void client.invalidateQueries({ queryKey: keys.me });
}

export { isUnauthorized, keys };
