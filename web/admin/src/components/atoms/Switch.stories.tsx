import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Switch } from "./Switch";

const meta: Meta<typeof Switch> = {
  title: "Atoms/Switch",
  component: Switch,
  tags: ["autodocs"],
};
export default meta;

type Story = StoryObj<typeof Switch>;

function Controlled() {
  const [v, setV] = useState(false);
  return <Switch checked={v} onCheckedChange={setV} label="기능 활성" />;
}

export const Default: Story = { render: () => <Controlled /> };
export const Checked: Story = {
  render: () => <Switch checked onCheckedChange={() => {}} label="활성" />,
};
export const Disabled: Story = {
  render: () => (
    <Switch checked={false} onCheckedChange={() => {}} disabled label="비활성" />
  ),
};
