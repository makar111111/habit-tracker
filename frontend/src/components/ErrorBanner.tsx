import { clearError, useActionError } from "../lib/errorBus";

/**
 * Смужка з повідомленням про невдалу дію.
 *
 * `role="alert"` змушує екранний диктор зачитати текст ОДРАЗУ, щойно
 * той з'явиться, не чекаючи, поки людина дійде до цього місця табом.
 * Для повідомлення про помилку це саме те, що треба: воно втрачає сенс,
 * якщо його помітять через хвилину.
 */
export function ErrorBanner() {
  const message = useActionError();

  if (message === null) return null;

  return (
    <p className="error error-banner" role="alert">
      <span>{message}</span>
      <button
        type="button"
        className="link-button"
        onClick={clearError}
        aria-label="Сховати повідомлення"
      >
        <span aria-hidden="true">✕</span>
      </button>
    </p>
  );
}
