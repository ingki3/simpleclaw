import type { Meta, StoryObj } from "@storybook/react";
import { MaskedSecretRow } from "./MaskedSecretRow";

const meta: Meta<typeof MaskedSecretRow> = {
  title: "Domain/MaskedSecretRow",
  component: MaskedSecretRow,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof MaskedSecretRow>;

export const Local: Story = {
  args: {
    label: "Claude API Key",
    keyName: "keyring:claude_api_key",
    scope: "local",
    lastFour: "1234",
  },
};

export const Prod: Story = {
  args: {
    label: "Telegram Bot Token",
    keyName: "keyring:telegram_bot_token",
    scope: "prod",
    lastFour: "9X2Z",
  },
};
