import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { RoutingTable } from "./RoutingTable";
import type { RoutingMap } from "@/lib/api/llm";

const meta: Meta<typeof RoutingTable> = {
  title: "LLM/RoutingTable",
  component: RoutingTable,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof RoutingTable>;

function Demo({ initial }: { initial: RoutingMap }) {
  const [v, setV] = useState<RoutingMap>(initial);
  return (
    <RoutingTable
      value={v}
      onChange={setV}
      providers={["claude", "openai", "gemini"]}
      fallback="gemini"
    />
  );
}

export const Empty: Story = { render: () => <Demo initial={{}} /> };

export const PartiallyFilled: Story = {
  render: () => (
    <Demo initial={{ general: "gemini", coding: "claude" }} />
  ),
};

export const Filled: Story = {
  render: () => (
    <Demo
      initial={{
        general: "gemini",
        coding: "claude",
        reasoning: "claude",
        tools: "openai",
      }}
    />
  ),
};
