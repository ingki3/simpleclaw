import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Tabs } from "./Tabs";

const meta: Meta<typeof Tabs> = {
  title: "Atoms/Tabs",
  component: Tabs,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof Tabs>;

export const Default: Story = {
  render: () => {
    const [tab, setTab] = useState<"skills" | "recipes">("skills");
    return (
      <Tabs<"skills" | "recipes">
        ariaLabel="스킬과 레시피"
        items={[
          { value: "skills", label: "스킬", count: 12 },
          { value: "recipes", label: "레시피", count: 4 },
        ]}
        value={tab}
        onValueChange={setTab}
      />
    );
  },
};
