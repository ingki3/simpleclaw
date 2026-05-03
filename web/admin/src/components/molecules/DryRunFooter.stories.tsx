import type { Meta, StoryObj } from "@storybook/react";
import { DryRunFooter } from "./DryRunFooter";

const meta: Meta<typeof DryRunFooter> = {
  title: "Molecules/DryRunFooter",
  component: DryRunFooter,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: {
    onCancel: () => {},
    onDryRun: () => {},
    onApply: () => {},
  },
};
export default meta;

type Story = StoryObj<typeof DryRunFooter>;

export const Dirty: Story = {
  args: { dirty: true, dryRunPassed: false },
};
export const DryRunPassed: Story = {
  args: {
    dirty: true,
    dryRunPassed: true,
    summary: "최근 1시간 트래픽 12건이 새 임계치에서 차단됩니다",
  },
};
export const Clean: Story = {
  args: { dirty: false, dryRunPassed: false },
};
