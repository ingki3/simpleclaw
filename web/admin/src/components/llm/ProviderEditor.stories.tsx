import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ProviderEditor } from "./ProviderEditor";
import type { ProviderConfig } from "@/lib/api/llm";

const meta: Meta<typeof ProviderEditor> = {
  title: "LLM/ProviderEditor",
  component: ProviderEditor,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof ProviderEditor>;

function Demo({ initial }: { initial: ProviderConfig }) {
  const [v, setV] = useState<ProviderConfig>(initial);
  return (
    <ProviderEditor
      name="claude"
      value={v}
      onChange={setV}
      modelOptions={["claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"]}
      role="primary"
      health={{ tone: "success", label: "정상" }}
    />
  );
}

export const Primary: Story = {
  render: () => (
    <Demo
      initial={{
        type: "api",
        model: "claude-opus-4-7",
        api_key: "keyring:claude_api_key",
        token_budget: 1_000_000,
        fallback_priority: 0,
        enabled: true,
      }}
    />
  ),
};

export const Disabled: Story = {
  render: () => (
    <Demo
      initial={{
        type: "api",
        model: "",
        api_key: "keyring:claude_api_key",
        enabled: false,
      }}
    />
  ),
};
