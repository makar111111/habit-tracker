/**
 * Хуки навколо TanStack Query — місце, де живе весь серверний стан.
 *
 * Головна ідея бібліотеки: дані з сервера — це не «стан компонента»,
 * а КЕШ. Він може застаріти, його треба оновлювати, кілька компонентів
 * мають бачити одне й те саме. Старий `app.js` розв'язував це тим, що
 * після кожної дії викликав `load()` і перемальовував усе. Працювало,
 * але одна відмітка коштувала повного перезавантаження списку.
 *
 * Тут інакше: мутація ОДРАЗУ править кеш (оптимістичне оновлення),
 * кнопка перемикається без очікування мережі, а справжня відповідь
 * сервера лише підтверджує вже намальоване.
 */

import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import * as api from "./client";
import { isUnauthorized } from "./client";
import { toISO } from "../lib/dates";
import type { Habit, HabitStats, HabitWithStats, User } from "./types";

/**
 * Ключі кешу в одному місці.
 *
 * Ключ — це масив, за яким TanStack Query впізнає запит. Якщо
 * розкидати рядки `["habits"]` по файлах, рано чи пізно десь буде
 * `["habit"]`, і кеш мовчки роздвоїться. Тому — єдине джерело.
 */
export const keys = {
  me: ["me"] as const,
  habits: ["habits"] as const,
  stats: ["stats"] as const,
  checkins: (habitId: number) => ["checkins", habitId] as const,
};

// ---------- Читання ----------

/**
 * Хто ми. Ця ж відповідь відповідає на питання «чи ми взагалі увійшли».
 *
 * `retry: false` тут обов'язковий. За замовчуванням бібліотека повторює
 * невдалий запит тричі — розумно для мережевого збою, але безглуздо
 * для 401: без cookie він і вчетверте буде 401. Без цього рядка екран
 * входу з'являвся б із затримкою в кілька секунд.
 */
export function useMe() {
  return useQuery<User>({
    queryKey: keys.me,
    queryFn: api.getMe,
    retry: false,
    // Один раз увійшли — і більше не смикаємо сервер на кожному фокусі
    // вкладки: хто ми такі, протягом сеансу не змінюється.
    staleTime: Infinity,
  });
}

export function useHabits(enabled: boolean) {
  return useQuery<Habit[]>({
    queryKey: keys.habits,
    queryFn: api.listHabits,
    enabled,
  });
}

export function useStats(enabled: boolean) {
  return useQuery<HabitStats[]>({
    queryKey: keys.stats,
    queryFn: api.listStats,
    enabled,
  });
}

/**
 * Звички і показники, склеєні в один список для компонентів.
 *
 * Два запити йдуть ПАРАЛЕЛЬНО — це не той випадок, коли другий
 * залежить від першого. Склейка — звичайний код, без useMemo:
 * тут лінійний прохід по кількох елементах, і мемоїзація коштувала б
 * більше, ніж економила.
 */
export function useHabitsWithStats(enabled: boolean) {
  const habits = useHabits(enabled);
  const stats = useStats(enabled);

  const byId = new Map((stats.data ?? []).map((s) => [s.habit_id, s]));

  const items: HabitWithStats[] = (habits.data ?? []).map((habit) => ({
    habit,
    // Порожні показники — коли звичку щойно створили, а /stats ще не
    // перезапитали. Малювати нуль чесніше, ніж не малювати рядок узагалі.
    stats: byId.get(habit.id) ?? emptyStats(habit.id),
  }));

  return {
    items,
    isLoading: habits.isLoading || stats.isLoading,
    error: habits.error ?? stats.error,
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

/** Усі відмітки однієї звички — для теплової карти й графіків. */
export function useCheckins(habitId: number, enabled = true) {
  return useQuery({
    queryKey: keys.checkins(habitId),
    queryFn: () => api.listCheckins(habitId),
    enabled,
  });
}

/**
 * Відмітки ВСІХ звичок одразу — потрібні екрану аналітики.
 *
 * `useQueries` замість циклу з `useQuery`: кількість звичок заздалегідь
 * невідома, а хуки не можна викликати в циклі зі змінною довжиною —
 * React звіряє їхній порядок між рендерами й на зміні довжини зламався б.
 * `useQueries` приймає масив описів і розв'язує цю задачу штатно.
 *
 * Ключі ті самі, що в `useCheckins`, тому дані спільні: розгорнутий
 * календар і графіки не ходять у мережу двічі за одним і тим самим.
 */
export function useAllCheckins(habitIds: number[], enabled: boolean) {
  const results = useQueries({
    queries: habitIds.map((id) => ({
      queryKey: keys.checkins(id),
      queryFn: () => api.listCheckins(id),
      enabled,
    })),
  });

  const daysByHabit = new Map<number, Set<string>>();
  habitIds.forEach((id, index) => {
    const data = results[index]?.data;
    if (data) daysByHabit.set(id, new Set(data.map((c) => c.day)));
  });

  return {
    daysByHabit,
    isLoading: results.some((r) => r.isLoading),
    error: results.find((r) => r.error)?.error ?? null,
  };
}

// ---------- Зміни ----------

/**
 * Перемкнути сьогоднішню відмітку.
 *
 * Найцікавіше місце всього фронтенду, тому докладно.
 *
 * `onMutate` спрацьовує ДО походу в мережу. Ми одразу правимо кеш
 * статистики, і React перемальовує кнопку в новий стан. Людина бачить
 * реакцію миттєво, навіть на поганому мобільному інтернеті.
 *
 * Якщо сервер відмовить, `onError` поверне збережений знімок кешу —
 * кнопка «відскочить» назад. Це і є відкат.
 */
export function useToggleCheckin() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: async ({ habitId, done }: { habitId: number; done: boolean }) => {
      const today = toISO(new Date());
      return done ? api.undoCheckIn(habitId, today) : api.checkIn(habitId, today);
    },

    onMutate: async ({ habitId, done }) => {
      // Скасовуємо запити статистики, що вже летять. Інакше відповідь
      // на старий запит могла б приземлитися ПІСЛЯ нашої правки й
      // затерти її старими даними — класична гонка.
      await client.cancelQueries({ queryKey: keys.stats });

      const snapshot = client.getQueryData<HabitStats[]>(keys.stats);

      client.setQueryData<HabitStats[]>(keys.stats, (old) =>
        (old ?? []).map((s) => (s.habit_id === habitId ? applyToggle(s, done) : s)),
      );

      return { snapshot };
    },

    onError: (_error, _variables, context) => {
      if (context?.snapshot) {
        client.setQueryData(keys.stats, context.snapshot);
      }
    },

    onSettled: (_data, _error, variables) => {
      // Хай там як закінчилося — питаємо сервер, як воно насправді.
      // Наша арифметика точна для серій, але не для longest_streak:
      // порахувати найдовшу серію за всю історію, маючи лише підсумки,
      // неможливо. Тому останнє слово лишається за сервером.
      void client.invalidateQueries({ queryKey: keys.stats });
      void client.invalidateQueries({ queryKey: keys.checkins(variables.habitId) });
    },
  });
}

/**
 * Передбачити, якою стане статистика після перемикання.
 *
 * Чому `current_streak` міняється рівно на одиницю в обидва боки:
 *
 * Ставимо відмітку. Серія або була нульовою (вчора теж порожньо) —
 * стане 1; або тривала з учорашнього дня і дорівнювала N — стане N+1.
 * В обох випадках +1.
 *
 * Знімаємо відмітку. Серія рахувалася з сьогодні й дорівнювала N.
 * Якщо вчора відмічено, серія виживе довжиною N−1; якщо ні — N було
 * одиницею, і стане 0, тобто теж N−1.
 *
 * Приємний збіг, але саме тому він тут і записаний: без пояснення
 * наступний читач вирішив би, що це груба заглушка.
 */
export function applyToggle(stats: HabitStats, wasDone: boolean): HabitStats {
  const delta = wasDone ? -1 : 1;
  return {
    ...stats,
    done_today: !wasDone,
    total: Math.max(0, stats.total + delta),
    current_streak: Math.max(0, stats.current_streak + delta),
    last_day: wasDone ? stats.last_day : toISO(new Date()),
  };
}

export function useCreateHabit() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ name, description }: { name: string; description: string }) =>
      api.createHabit(name, description),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.habits });
      void client.invalidateQueries({ queryKey: keys.stats });
    },
  });
}

export function useDeleteHabit() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: (habitId: number) => api.deleteHabit(habitId),

    onMutate: async (habitId) => {
      await client.cancelQueries({ queryKey: keys.habits });
      const snapshot = client.getQueryData<Habit[]>(keys.habits);

      // Прибираємо рядок одразу: видалення — дія, після якої чекати
      // на мережу найнеприємніше, бо елемент лишається на екрані
      // і виглядає так, ніби натискання не спрацювало.
      client.setQueryData<Habit[]>(keys.habits, (old) =>
        (old ?? []).filter((h) => h.id !== habitId),
      );

      return { snapshot };
    },

    onError: (_error, _habitId, context) => {
      if (context?.snapshot) client.setQueryData(keys.habits, context.snapshot);
    },

    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.habits });
      void client.invalidateQueries({ queryKey: keys.stats });
    },
  });
}

export function useRenameHabit() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ habitId, name }: { habitId: number; name: string }) =>
      api.updateHabit(habitId, { name }),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.habits }),
  });
}

export function useLogout() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      // Чистимо ВЕСЬ кеш, а не окремі ключі. Лишити чужі звички
      // в пам'яті після виходу — це те, чого робити не можна:
      // наступний, хто увійде в цьому браузері, побачив би їх
      // на частку секунди, поки не приїдуть його власні.
      client.clear();
    },
  });
}

/** Після успішного входу: скинути «ми не автентифіковані» і перезапитати все. */
export function markLoggedIn(client: QueryClient): void {
  void client.invalidateQueries();
}

export { isUnauthorized };
