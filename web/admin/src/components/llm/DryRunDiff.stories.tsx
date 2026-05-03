import type { Meta, StoryObj } from "@storybook/react";
import { DryRunDiff } from "./DryRunDiff";

const meta: Meta<typeof DryRunDiff> = {
  title: "LLM/DryRunDiff",
  component: DryRunDiff,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof DryRunDiff>;

export const Empty: Story = { args: { result: null } };
export const Loading: Story = { args: { result: null, loading: true } };

export const HotChange: Story = {
  args: {
    result: {
      outcome: "dry_run",
      diff: {
        before: { default: "gemini", providers: { claude: { model: "claude-opus-4-6" } } },
        after: { default: "claude", providers: { claude: { model: "claude-opus-4-7" } } },
      },
      policy: {
        level: "Hot",
        requires_restart: false,
        affected_modules: ["llm.router"],
        matched_keys: ["llm.default", "llm.providers.claude.model"],
      },
    },
  },
};

export const ServiceRestartChange: Story = {
  args: {
    result: {
      outcome: "dry_run",
      diff: {
        before: { providers: { mcp_x: { type: "api" } } },
        after: { providers: { mcp_x: { type: "cli" } } },
      },
      policy: {
        level: "Service-restart",
        requires_restart: true,
        affected_modules: ["llm.router", "secrets"],
        matched_keys: ["llm.providers.mcp_x.type"],
      },
    },
  },
};
