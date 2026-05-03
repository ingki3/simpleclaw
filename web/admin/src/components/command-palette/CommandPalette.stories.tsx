import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { CommandPalette } from "./CommandPalette";
import { Button } from "@/components/atoms/Button";
import type { SecretMeta } from "@/lib/api";

const meta: Meta<typeof CommandPalette> = {
  title: "Primitives/CommandPalette",
  component: CommandPalette,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof CommandPalette>;

const SAMPLE_SECRETS: SecretMeta[] = [
  { name: "claude_api_key", backend: "keyring", last_rotated_at: "2026-04-19T12:00:00Z" },
  { name: "openai_api_key", backend: "keyring", last_rotated_at: null },
  { name: "telegram_bot_token", backend: "env", last_rotated_at: null },
];

function Frame({ secrets }: { secrets?: SecretMeta[] }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="grid min-h-screen place-items-center bg-[--background] p-10">
      <Button onClick={() => setOpen(true)}>⌘K 열기</Button>
      <CommandPalette open={open} onOpenChange={setOpen} secrets={secrets} />
    </div>
  );
}

export const PagesOnly: Story = {
  render: () => <Frame />,
};

export const WithSecrets: Story = {
  render: () => <Frame secrets={SAMPLE_SECRETS} />,
};
