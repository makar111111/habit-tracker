/**
 * Типи відповідей API.
 *
 * Це ручний переклад pydantic-моделей із `models.py` на TypeScript.
 * Ручний — бо генератор (openapi-typescript) додав би ще один інструмент
 * і крок збірки заради восьми невеликих типів. Ціна вибору: якщо хтось
 * змінить поле в `models.py`, тут це саме по собі не оновиться.
 *
 * Страховка від розсинхрону — не дисципліна, а тест: `test_frontend_contract.py`
 * у корені проєкту читає цей файл і звіряє набір полів із pydantic-моделями.
 * Перейменував поле лише в одному місці — тест червоний.
 */

/** Звичка. Дзеркало `HabitPublic`. */
export interface Habit {
  id: number;
  name: string;
  /** У базі NOT NULL із порожнім рядком за замовчуванням, тому не `null`. */
  description: string;
}

/**
 * Показники звички. Дзеркало `HabitStats`.
 *
 * Назви полів — у snake_case, бо так їх віддає Python. Спокуса
 * перейменувати в camelCase на межі велика, але кожне перейменування —
 * це місце, де можна помилитись мовчки. Лишаємо як є: одна назва
 * від бази до кнопки.
 */
export interface HabitStats {
  habit_id: number;
  total: number;
  current_streak: number;
  longest_streak: number;
  done_today: boolean;
  /** ISO-рядок `2026-09-12` або `null`, якщо відміток ще не було. */
  last_day: string | null;
}

/** Одна відмітка. Дзеркало таблиці `Checkin`. */
export interface Checkin {
  id: number;
  habit_id: number;
  /** ISO-рядок `2026-09-12`. JSON не має типу «дата», тому приходить рядком. */
  day: string;
}

/** Поточний користувач. Дзеркало `UserPublic`. */
export interface User {
  id: number;
  telegram_id: number | null;
  name: string;
}

/** Відповідь на `POST /auth/login-code`. */
export interface LoginCode {
  token: string;
  /** Готове посилання виду `https://t.me/бот?start=код`. */
  url: string;
  /** Скільки секунд код лишається дійсним. */
  expires_in: number;
}

/** Відповідь на `GET /auth/login-code/{token}`. */
export interface LoginPoll {
  status: "pending" | "confirmed";
}

/**
 * Звичка разом із її показниками — те, що потрібно кожному рядку списку.
 *
 * В API це дві окремі відповіді (`/habits` і `/stats`), і так правильно:
 * ендпоінт статистики вміє рахувати все одним запитом до бази. Склеюємо
 * їх уже тут, на клієнті, бо саме компонентам зручно мати одне ціле.
 */
export interface HabitWithStats {
  habit: Habit;
  stats: HabitStats;
}
