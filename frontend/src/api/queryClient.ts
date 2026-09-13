import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { reportError, resetErrorBus } from "../lib/errorBus";
import { isUnauthorized } from "./client";
import { keys } from "./keys";

const sessionVersions = new WeakMap<QueryClient, number>();

/** Версія змінюється при виході, щоб пізні відповіді старого сеансу ігнорувалися. */
export function sessionVersion(client: QueryClient): number {
  return sessionVersions.get(client) ?? 0;
}

/** При завершенні сеансу приватні запити скасовуються, а профіль стає порожнім. */
export function endSession(client: QueryClient): void {
  sessionVersions.set(client, sessionVersion(client) + 1);
  void client.cancelQueries();
  client.setQueryData(keys.me, null);
  resetErrorBus();
}

/** Видалення активного query створює новий запит; чистимо після unmount екранів. */
export function clearPrivateQueries(client: QueryClient): void {
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== keys.me[0] });
}

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    queryCache: new QueryCache({
      onError: (error) => { if (isUnauthorized(error)) endSession(client); },
    }),
    // Помилки всіх дій показує один ErrorBanner; компоненти не приховують відмови.
    mutationCache: new MutationCache({
      onError: (error) => {
        if (isUnauthorized(error)) endSession(client);
        else reportError(error instanceof Error ? error.message : "Не вдалося виконати дію");
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (count, error) => !isUnauthorized(error) && count < 1,
        refetchOnWindowFocus: true,
      },
    },
  });
  // Статус уже завершений: дві одночасні onSettled можуть обидві бачити іншу
  // мутацію як pending. Подія останнього завершення дає надійну точку оновлення.
  client.getMutationCache().subscribe((event) => {
    if (event.type !== "updated" || !["success", "error"].includes(event.action.type)) return;
    if (event.mutation.options.mutationKey?.[0] !== keys.habitChange[0]) return;
    if (isUnauthorized(event.mutation.state.error) || client.getQueryData(keys.me) === null) return;
    if (client.isMutating({ mutationKey: keys.habitChange }) === 0) {
      void client.invalidateQueries({ queryKey: keys.habits });
      void client.invalidateQueries({ queryKey: keys.stats });
    }
  });
  return client;
}
