import type { Meta, StoryObj } from "@storybook/react";
import { MemoryEntryRow } from "./MemoryEntryRow";
import type { MemoryEntry } from "@/lib/api/memory";

const baseEntry: MemoryEntry = {
  id: "1:0",
  sectionIndex: 1,
  section: "2026-04-28",
  lineIndex: 0,
  text: "사용자는 한국어 존댓말을 선호한다.",
  type: "user",
};

const meta: Meta<typeof MemoryEntryRow> = {
  title: "Domain/MemoryEntryRow",
  component: MemoryEntryRow,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
  args: {
    entry: baseEntry,
    onSave: () => Promise.resolve(),
    onRequestDelete: () => {},
  },
};
export default meta;
type Story = StoryObj<typeof MemoryEntryRow>;

export const TypedUser: Story = {
  render: (args) => (
    <ul className="rounded-[--radius-m] border border-[--border]">
      <MemoryEntryRow {...args} />
    </ul>
  ),
};

export const NoType: Story = {
  args: {
    entry: { ...baseEntry, id: "2:1", type: null, text: "타입 없는 자유 항목." },
  },
  render: (args) => (
    <ul className="rounded-[--radius-m] border border-[--border]">
      <MemoryEntryRow {...args} />
    </ul>
  ),
};

export const TypedFeedback: Story = {
  args: {
    entry: {
      ...baseEntry,
      type: "feedback",
      text: "Mock된 DB로 테스트하지 않는다 — 마이그레이션이 깨질 수 있어요.",
    },
  },
  render: (args) => (
    <ul className="rounded-[--radius-m] border border-[--border]">
      <MemoryEntryRow {...args} />
    </ul>
  ),
};

export const Disabled: Story = {
  args: { disabled: true },
  render: (args) => (
    <ul className="rounded-[--radius-m] border border-[--border]">
      <MemoryEntryRow {...args} />
    </ul>
  ),
};

export const LongBody: Story = {
  args: {
    entry: {
      ...baseEntry,
      text:
        "이건 굉장히 긴 항목입니다. 한 줄에 다 들어가지 않으니 자연 줄바꿈이 일어나야 하고, 편집 시에는 textarea가 자동으로 늘어나야 합니다. 백엔드 토큰 카운트는 chars/4 휴리스틱으로 추정합니다.",
    },
  },
  render: (args) => (
    <ul className="rounded-[--radius-m] border border-[--border]">
      <MemoryEntryRow {...args} />
    </ul>
  ),
};
