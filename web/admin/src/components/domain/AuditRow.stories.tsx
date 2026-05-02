import type { Meta, StoryObj } from "@storybook/react";
import { AuditRow } from "./AuditRow";

const meta: Meta<typeof AuditRow> = {
  title: "Domain/AuditRow",
  component: AuditRow,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: {
    onUndo: () => {},
    onViewTrace: () => {},
  },
  decorators: [
    (Story) => (
      <ul className="w-full max-w-xl rounded-[--radius-l] border border-[--border] bg-[--card]">
        <Story />
      </ul>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof AuditRow>;

export const Applied: Story = {
  args: {
    entry: {
      id: "a1",
      action: "config.update",
      target: "llm.providers.claude.model",
      before: "claude-sonnet-4-20250514",
      after: "claude-opus-4-20250514",
      actor: "local",
      at: "23:30",
      traceId: "01HW1234ABCDEF",
      outcome: { tone: "success", label: "applied" },
      undoable: true,
    },
  },
};
