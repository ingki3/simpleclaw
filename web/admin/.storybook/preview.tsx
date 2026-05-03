import type { Preview } from "@storybook/react";
import "../src/app/globals.css";
import { ToastProvider } from "../src/lib/toast";

/**
 * Storybook preview — 디자인 토큰을 그대로 노출하기 위해 globals.css를 임포트한다.
 * 라이트/다크 토글은 globals 토글로 노출 (story 내부에서 분기 코드를 쓰지 않는다).
 */
const preview: Preview = {
  parameters: {
    layout: "centered",
    a11y: { config: {} },
  },
  globalTypes: {
    theme: {
      description: "테마",
      defaultValue: "light",
      toolbar: {
        title: "Theme",
        icon: "paintbrush",
        items: [
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
          { value: "system", title: "System" },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [
    (Story, ctx) => {
      const theme = ctx.globals.theme as "light" | "dark" | "system";
      if (typeof document !== "undefined") {
        const root = document.documentElement;
        root.classList.remove("theme-light", "theme-dark");
        if (theme === "light") root.classList.add("theme-light");
        if (theme === "dark") root.classList.add("theme-dark");
      }
      return (
        <ToastProvider>
          <Story />
        </ToastProvider>
      );
    },
  ],
};
export default preview;
