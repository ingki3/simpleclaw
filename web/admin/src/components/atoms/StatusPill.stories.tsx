import type { Meta, StoryObj } from "@storybook/react";
import { StatusPill } from "./StatusPill";

const meta: Meta<typeof StatusPill> = {
  title: "Atoms/StatusPill",
  component: StatusPill,
  tags: ["autodocs"],
  args: { children: "정상" },
};
export default meta;

type Story = StoryObj<typeof StatusPill>;

export const Success: Story = { args: { tone: "success" } };
export const Warning: Story = {
  args: { tone: "warning", children: "재시도 중" },
};
export const Error: Story = {
  args: { tone: "error", children: "연결 실패" },
};
export const Info: Story = { args: { tone: "info", children: "준비 중" } };
export const Neutral: Story = { args: { tone: "neutral", children: "유휴" } };
