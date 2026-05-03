import type { Meta, StoryObj } from "@storybook/react";
import { DreamingProgressCard } from "./DreamingProgressCard";

const meta: Meta<typeof DreamingProgressCard> = {
  title: "Domain/DreamingProgressCard",
  component: DreamingProgressCard,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: { onTrigger: () => {} },
};
export default meta;
type Story = StoryObj<typeof DreamingProgressCard>;

export const Idle: Story = {
  args: {
    state: {
      running: false,
      step: null,
      stepLabel: null,
      startedAt: null,
      lastFinishedAt: null,
      lastOutcome: null,
      lastMessage: null,
    },
  },
};

export const RunningStep2: Story = {
  args: {
    state: {
      running: true,
      step: 2,
      stepLabel: "LLM 요약",
      startedAt: new Date().toISOString(),
      lastFinishedAt: null,
      lastOutcome: null,
      lastMessage: null,
    },
  },
};

export const LastSuccess: Story = {
  args: {
    state: {
      running: false,
      step: null,
      stepLabel: null,
      startedAt: null,
      lastFinishedAt: new Date(Date.now() - 30_000).toISOString(),
      lastOutcome: "success",
      lastMessage: "5건의 새 항목이 추가됐어요.",
    },
  },
};

export const LastFailure: Story = {
  args: {
    state: {
      running: false,
      step: null,
      stepLabel: null,
      startedAt: null,
      lastFinishedAt: new Date(Date.now() - 60_000).toISOString(),
      lastOutcome: "failure",
      lastMessage: "LLM 응답 시간 초과 — 다시 시도해 주세요.",
    },
  },
};
