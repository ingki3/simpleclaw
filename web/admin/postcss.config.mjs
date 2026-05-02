/**
 * Tailwind v4는 PostCSS 플러그인 한 줄로 통합된다.
 * 별도의 tailwind.config.{js,ts}는 사용하지 않으며, 모든 토큰은 globals.css의 `@theme`에서 정의한다.
 */
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
