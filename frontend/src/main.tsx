import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { createQueryClient } from "./api/queryClient";
import "./styles/tokens.css";
import "./styles/app.css";

const queryClient = createQueryClient();

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
