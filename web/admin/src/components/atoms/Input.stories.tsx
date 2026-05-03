import type { Meta, StoryObj } from "@storybook/react";
import { Search } from "lucide-react";
import { Input } from "./Input";

const meta: Meta<typeof Input> = {
  title: "Atoms/Input",
  component: Input,
  tags: ["autodocs"],
  args: { placeholder: "값을 입력하세요" },
};
export default meta;

type Story = StoryObj<typeof Input>;

export const Default: Story = {};
export const WithLeftIcon: Story = {
  args: { leftIcon: <Search size={14} aria-hidden /> },
};
export const Invalid: Story = {
  args: { invalid: true, defaultValue: "비정상 값" },
};
