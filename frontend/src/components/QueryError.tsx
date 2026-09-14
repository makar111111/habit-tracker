interface Props {
  error: Error;
  retry: () => unknown;
  busy?: boolean;
}

export function QueryError({ error, retry, busy = false }: Props) {
  return (
    <div className="error query-error" role="alert">
      <span>{error.message}</span>
      <button
        type="button"
        className="link-button"
        disabled={busy}
        onClick={() => {
          void retry();
        }}
      >
        {busy ? "Повторюю…" : "Спробувати ще раз"}
      </button>
    </div>
  );
}
