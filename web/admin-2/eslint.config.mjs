/**
 * ESLint flat config — Admin 2.0.
 *
 * Next.js 16 부터 `next lint` 가 제거되어 ESLint 를 직접 호출한다.
 * S0 단계에서는 typescript-eslint 권장 룰만 적용한다.
 * S1 이후 라우트가 늘어나면 `@next/eslint-plugin-next` 의 React-Hooks 규칙을 추가한다.
 */
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // 브라우저에서 실행되는 React 코드.
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
  },
  {
    // Node 스크립트(run-next, vitest config, playwright config).
    files: [
      "scripts/**/*.{js,mjs,ts}",
      "*.config.{js,mjs,ts}",
      "tests/**/*.{ts,tsx}",
    ],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
);
