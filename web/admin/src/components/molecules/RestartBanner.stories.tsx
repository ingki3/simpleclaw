import type { Meta, StoryObj } from "@storybook/react";
import { RestartBanner } from "./RestartBanner";

const meta: Meta<typeof RestartBanner> = {
  title: "Molecules/RestartBanner",
  component: RestartBanner,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: {
    pending: 3,
    onRestartNow: () => {},
    onDeferUntilNextStart: () => {},
  },
};
export default meta;

type Story = StoryObj<typeof RestartBanner>;

export const Default: Story = {};
