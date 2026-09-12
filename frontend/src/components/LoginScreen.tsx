import { useEffect, useRef, useState } from "react";

import * as api from "../api/client";
import type { LoginCode } from "../api/types";

const POLL_INTERVAL_MS = 2000;

interface Props {
  /** Викликається, коли бот підтвердив код і cookie вже стоїть. */
  onSuccess: () => void;
}

/**
 * Екран входу.
 *
 * Потік такий: браузер просить одноразовий код → відкриває бота за
 * посиланням `t.me/бот?start=код` → людина тисне там «Так, це я» →
 * браузер, який усі ці секунди перепитує сервер, отримує підтвердження
 * і разом із ним cookie сесії.
 *
 * Чому не віджет Telegram Login: він вимагає публічного домену і не
 * працює на localhost. Цей спосіб працює скрізь однаково й заразом
 * доводить, що бот і сайт — це один акаунт.
 */
export function LoginScreen({ onSuccess }: Props) {
  const [code, setCode] = useState<LoginCode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // useRef, а не useState: зміна таймера не має перемальовувати екран.
  // Тримати його в стані означало б зайвий рендер кожні дві секунди.
  const timerRef = useRef<number | null>(null);

  // Прибирання за собою при знятті компонента. Без цього таймер
  // продовжив би стукати в сервер уже після успішного входу —
  // а перший же запит згорілим кодом дав би 404 і "помилку" на
  // екрані, якого вже немає.
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
  }, []);

  async function start() {
    setBusy(true);
    setError(null);

    let data: LoginCode;
    try {
      data = await api.requestLoginCode();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не вдалося почати вхід");
      setBusy(false);
      return;
    }

    setCode(data);

    // Відкриваємо бота одразу, щоб не змушувати робити зайвий клік.
    // Якщо браузер заблокує вікно — посилання лишається на екрані.
    window.open(data.url, "_blank", "noopener");

    poll(data);
  }

  function poll(data: LoginCode) {
    const deadline = Date.now() + data.expires_in * 1000;

    timerRef.current = window.setInterval(async () => {
      if (Date.now() > deadline) {
        stopPolling();
        fail("Час вийшов. Спробуй ще раз.");
        return;
      }

      try {
        const result = await api.pollLoginCode(data.token);
        if (result.status === "confirmed") {
          stopPolling();
          onSuccess();
        }
      } catch (err) {
        // 404 «код недійсний» або 410 «протермінований» — далі питати
        // марно, код у базі вже не існує.
        stopPolling();
        fail(err instanceof Error ? err.message : "Код став недійсним");
      }
    }, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function fail(message: string) {
    setError(message);
    setCode(null);
    setBusy(false);
  }

  return (
    <main className="login">
      <h1>Трекер звичок</h1>
      <p>Щоб побачити свої звички, увійди через Telegram — той самий акаунт, що й у боті.</p>

      {error && <p className="error">{error}</p>}

      {code === null ? (
        <button type="button" className="primary" onClick={start} disabled={busy}>
          {busy ? "Готую код…" : "Увійти через Telegram"}
        </button>
      ) : (
        <>
          <a className="login-link" href={code.url} target="_blank" rel="noopener noreferrer">
            Відкрити бота
          </a>
          <p className="login-hint">
            …і натисни там «Так, це я». Чекаю на підтвердження
            <span aria-hidden="true">…</span>
          </p>
        </>
      )}
    </main>
  );
}
