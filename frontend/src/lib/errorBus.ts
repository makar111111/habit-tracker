import { useSyncExternalStore } from "react";

/**
 * Одне місце, куди стікаються повідомлення про невдалі дії.
 *
 * Навіщо окреме сховище, а не звичайний useState у компоненті: дії
 * запускаються глибоко — з рядка звички, з форми додавання, — а
 * показувати помилку треба вгорі сторінки, де її видно. Передавати
 * функцію `покажиПомилку` через усі проміжні компоненти означало б
 * тягнути її крізь ті, яким вона не потрібна.
 *
 * Стара версія застосунку розв'язувала це інакше: кожна дія сама
 * викликала `showError` у своєму catch — чотири виклики в чотирьох
 * місцях. Працювало, поки хтось не додавав п'яту дію й не забував
 * про catch. Тут повідомлення приходить автоматично, бо підключене
 * до MutationCache — спільного місця, крізь яке проходять УСІ мутації.
 *
 * `useSyncExternalStore` — штатний спосіб підписати React на дані,
 * що живуть поза ним. Він гарантує, що під час одного рендера всі
 * компоненти побачать один і той самий знімок.
 */

let current: string | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

export function reportError(message: string): void {
  current = message;
  emit();
}

export function clearError(): void {
  if (current === null) return;
  current = null;
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// getSnapshot МАЄ повертати те саме значення, поки дані не змінились.
// Якби тут будувався новий об'єкт, React вирішив би, що стан міняється
// на кожній перевірці, і зациклився б на нескінченних рендерах.
function getSnapshot(): string | null {
  return current;
}

export function useActionError(): string | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Лише для тестів: повернути сховище до чистого стану. */
export function resetErrorBus(): void {
  current = null;
  listeners.clear();
}
