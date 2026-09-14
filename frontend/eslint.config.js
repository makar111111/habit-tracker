import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Лінтер для фронтенду. Запуск: npm run lint
//
// `export default` тут вимушений, як і у vite.config.ts: ESLint шукає
// налаштування саме в експорті за замовчуванням.
export default tseslint.config(
  { ignores: ["dist", "coverage"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      // Правила хуків React — головна причина мати ESLint поруч із tsc.
      // Типи не бачать, що хук викликано під `if` або що ефект читає
      // змінну, якої немає в масиві залежностей. Результат — застарілі
      // дані на екрані без жодної помилки.
      ...reactHooks.configs.recommended.rules,
      "no-restricted-exports": ["error", { restrictDefaultExports: { direct: true, named: true, defaultFrom: true, namedFrom: true, namespaceFrom: true } }],
    },
  },
  {
    // Єдині файли, яким `export default` дозволено: інструменти читають саме його.
    files: ["vite.config.ts", "eslint.config.js"],
    rules: { "no-restricted-exports": "off" },
  },
);
