import type { Meta, StoryObj } from "@storybook/react";
import { PolicyPill } from "./PolicyPill";

const meta: Meta<typeof PolicyPill> = {
  title: "Atoms/PolicyPill",
  component: PolicyPill,
  tags: ["autodocs"],
};
export default meta;

type Story = StoryObj<typeof PolicyPill>;

export const Hot: Story = { args: { level: "hot" } };
export const ServiceRestart: Story = { args: { level: "service-restart" } };
export const ProcessRestart: Story = { args: { level: "process-restart" } };
