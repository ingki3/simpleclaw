import type { Meta, StoryObj } from "@storybook/react";
import { Badge } from "./Badge";

const meta: Meta<typeof Badge> = {
  title: "Atoms/Badge",
  component: Badge,
  tags: ["autodocs"],
  args: { children: "Hot" },
};
export default meta;

type Story = StoryObj<typeof Badge>;

export const Neutral: Story = { args: { tone: "neutral" } };
export const Success: Story = { args: { tone: "success", children: "정상" } };
export const Warning: Story = { args: { tone: "warning", children: "주의" } };
export const Danger: Story = { args: { tone: "danger", children: "실패" } };
export const Info: Story = { args: { tone: "info", children: "안내" } };
export const Brand: Story = { args: { tone: "brand", children: "Primary" } };
