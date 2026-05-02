import type { StorybookConfig } from "@storybook/nextjs";

/**
 * Storybook 설정 — Next.js 16 + Tailwind v4 통합.
 *
 * 모든 atomic/molecular/domain 컴포넌트의 스토리는 src/**/*.stories.tsx 패턴으로 자동 수집.
 */
const config: StorybookConfig = {
  framework: "@storybook/nextjs",
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-essentials", "@storybook/addon-a11y"],
  staticDirs: ["../public"],
  docs: { autodocs: "tag" },
};
export default config;
