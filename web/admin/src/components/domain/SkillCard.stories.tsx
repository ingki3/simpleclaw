import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { SkillCard } from "./SkillCard";
import type { Skill } from "@/lib/skills-types";

const sample: Skill = {
  id: "gmail-skill",
  name: "gmail-skill",
  description: "Gmail에서 메일을 검색하고 읽는 스킬",
  enabled: true,
  source: "global",
  directory: "~/.agents/skills/gmail-skill",
  argument_hint: "검색어 또는 메일 ID",
  user_invocable: true,
  retry_policy: { max_attempts: 3, backoff_seconds: 2, backoff_strategy: "exponential" },
  last_run: {
    started_at: new Date(Date.now() - 12 * 60_000).toISOString(),
    status: "ok",
    duration_ms: 1820,
  },
};

const meta: Meta<typeof SkillCard> = {
  title: "Domain/SkillCard",
  component: SkillCard,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof SkillCard>;

export const Default: Story = {
  render: () => {
    const [skill, setSkill] = useState(sample);
    return (
      <div className="max-w-[320px]">
        <SkillCard
          skill={skill}
          onSelect={() => {}}
          onToggleEnabled={(_id, next) =>
            setSkill((s) => ({ ...s, enabled: next }))
          }
        />
      </div>
    );
  },
};

export const Disabled: Story = {
  render: () => {
    const [skill, setSkill] = useState({
      ...sample,
      enabled: false,
      last_run: {
        started_at: new Date(Date.now() - 3 * 24 * 3600_000).toISOString(),
        status: "error" as const,
        duration_ms: 8200,
        error: "rate-limit",
      },
    });
    return (
      <div className="max-w-[320px]">
        <SkillCard
          skill={skill}
          onSelect={() => {}}
          onToggleEnabled={(_id, next) =>
            setSkill((s) => ({ ...s, enabled: next }))
          }
        />
      </div>
    );
  },
};
