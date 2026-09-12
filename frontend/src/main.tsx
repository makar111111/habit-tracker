import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import "./styles/tokens.css";
import "./styles/app.css";

/**
 * Налаштування кешу на весь застосунок.
 *
 * staleTime — скільки часу дані вважаються свіжими. Нуль (за
 * замовчуванням) означає «перезапитуй за найменшого приводу»: кожне
 * повернення на вкладку, кожне монтування компонента. Для трекера
 * звичок це марна метушня — дані міняються лише тоді, коли їх міняємо
 * ми самі, і тоді ми явно скидаємо кеш у мутаціях.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Одна спроба повтору замість трьох. Сервер тут свій, локальний:
      // якщо не відповів двічі — він лежить, і чекати ще два рази
      // означає лише довше показувати людині порожній екран.
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const container = document.getElementById("root");
if (!container) {
  // Такого статися не може — вузол прописаний в index.html. Але
  // getElementById за типом повертає `| null`, і мовчки придушити це
  // через `!` означало б отримати незрозуміле падіння замість
  // зрозумілого повідомлення, якщо колись хтось правитиме розмітку.
  throw new Error("Не знайдено вузол #root — розмітка index.html зламана");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
