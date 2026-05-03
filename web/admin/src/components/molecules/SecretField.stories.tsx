import type { Meta, StoryObj } from "@storybook/react";
import { SecretField } from "./SecretField";

const meta: Meta<typeof SecretField> = {
  title: "Molecules/SecretField",
  component: SecretField,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof SecretField>;

export const Default: Story = {
  args: {
    name: "keyring:claude_api_key",
    lastFour: "1234",
    plaintext: "sk-ant-fake-secret-1234",
  },
};
