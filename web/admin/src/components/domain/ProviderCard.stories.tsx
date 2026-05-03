import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ProviderCard } from "./ProviderCard";

const meta: Meta<typeof ProviderCard> = {
  title: "Domain/ProviderCard",
  component: ProviderCard,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof ProviderCard>;

function Demo() {
  const [enabled, setEnabled] = useState(true);
  return (
    <ProviderCard
      name="Claude"
      model="claude-opus-4-7"
      enabled={enabled}
      onEnabledChange={setEnabled}
      role="primary"
      health={{ tone: "success", label: "ping 38ms" }}
    />
  );
}

export const Default: Story = { render: () => <Demo /> };
