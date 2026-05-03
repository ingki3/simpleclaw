import type { Meta, StoryObj } from "@storybook/react";
import { SettingCard } from "./SettingCard";
import { PolicyPill } from "@/components/atoms/PolicyPill";
import { Button } from "@/components/atoms/Button";
import { Input } from "@/components/atoms/Input";

const meta: Meta<typeof SettingCard> = {
  title: "Molecules/SettingCard",
  component: SettingCard,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof SettingCard>;

export const Default: Story = {
  args: {
    title: "Webhook Rate Limit",
    description: "단위 시간당 처리할 요청 수를 제한합니다.",
    headerRight: <PolicyPill level="hot" />,
    health: { tone: "success", label: "정상" },
    children: (
      <div className="grid grid-cols-2 gap-4">
        <Input placeholder="60" defaultValue="60" />
        <Input placeholder="60s" defaultValue="60s" />
      </div>
    ),
    footer: (
      <>
        <Button variant="ghost" size="sm">
          취소
        </Button>
        <Button variant="primary" size="sm">
          적용
        </Button>
      </>
    ),
  },
};
