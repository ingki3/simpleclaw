import type { Meta, StoryObj } from "@storybook/react";
import { CronJobRow } from "./CronJobRow";

const meta: Meta<typeof CronJobRow> = {
  title: "Domain/CronJobRow",
  component: CronJobRow,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: {
    onTogglePause: () => {},
    onDelete: () => {},
  },
  // 행 단위 컴포넌트라 table wrapper가 필요하다.
  decorators: [
    (Story) => (
      <table className="w-full border border-(--border) bg-(--card)">
        <tbody>
          <Story />
        </tbody>
      </table>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof CronJobRow>;

export const Healthy: Story = {
  args: {
    job: {
      id: "j1",
      name: "morning-brief",
      schedule: "0 9 * * *",
      nextRun: "내일 09:00",
      status: { tone: "success", label: "정상" },
      circuit: "closed",
      paused: false,
    },
  },
};

export const Failing: Story = {
  args: {
    job: {
      id: "j2",
      name: "weekly-digest",
      schedule: "0 18 * * 5",
      nextRun: "금 18:00",
      status: { tone: "error", label: "3회 연속 실패" },
      circuit: "open",
      paused: false,
    },
  },
};
