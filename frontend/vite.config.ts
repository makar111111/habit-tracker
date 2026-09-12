// defineConfig беремо з vitest/config, а не з vite: це та сама функція,
// але з доданим полем `test` у типі. Через vite TypeScript відкидав би
// секцію тестів як невідому властивість.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Шляхи, які належать API, а не фронтенду. У режимі розробки Vite тримає
// власний сервер на порту 5173, і запит на /habits звідти пішов би в нікуди.
// Проксі пересилає їх на uvicorn — для браузера все лишається одним
// походженням (origin), а це критично: cookie сесії має атрибут SameSite,
// і на "чужий" порт браузер її просто не надішле.
const API_ROUTES = [
  "/habits",
  "/stats",
  "/auth",
  "/users",
  "/telegram-users",
  "/docs",
  "/openapi.json",
];

const proxy = Object.fromEntries(
  API_ROUTES.map((route) => [route, { target: "http://127.0.0.1:8000", changeOrigin: true }]),
);

// Vite читає саме `export default` — це його контракт, а не наш вибір стилю.
// Правило проєкту "лише іменовані експорти" стосується нашого коду;
// конфіги інструментів живуть за правилами тих інструментів.
export default defineConfig({
  plugins: [react()],

  // Застосунок віддається не з кореня, а з /app/ (див. app.mount у main.py).
  // Без base зібраний index.html посилався б на /assets/index.js замість
  // /app/assets/index.js — і сторінка відкрилася б порожньою.
  base: "/app/",

  build: {
    outDir: "dist",
    // Мапи вихідного коду в бойовій збірці: у Sources браузера видно
    // справжній TSX, а не мінімізовану кашу. Для навчального проєкту,
    // який показують на співбесіді, це радше плюс, ніж витік.
    sourcemap: true,
  },

  server: { proxy },

  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
