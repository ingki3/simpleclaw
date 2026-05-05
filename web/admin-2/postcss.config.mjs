/**
 * Tailwind v4는 PostCSS 플러그인 한 줄로 통합된다.
 * 별도의 tailwind.config.{js,ts}는 사용하지 않으며, 모든 토큰은
 * S1 (Design System) 단계에서 globals.css의 `@theme`에 정의된다.
 */
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
