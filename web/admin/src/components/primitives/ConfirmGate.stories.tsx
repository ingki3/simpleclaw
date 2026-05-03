import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { ConfirmGate } from "./ConfirmGate";
import { Button } from "@/components/atoms/Button";

const meta: Meta<typeof ConfirmGate> = {
  title: "Primitives/ConfirmGate",
  component: ConfirmGate,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ConfirmGate>;

function Demo({
  args,
}: {
  args: Partial<React.ComponentProps<typeof ConfirmGate>>;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="grid min-h-screen place-items-center bg-[--background] p-10">
      <Button variant="destructive" onClick={() => setOpen(true)}>
        시크릿 회전
      </Button>
      <ConfirmGate
        {...(args as React.ComponentProps<typeof ConfirmGate>)}
        open={open}
        onOpenChange={setOpen}
      />
    </div>
  );
}

export const RotateSecret: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "Claude API 키를 회전할까요?",
    description: "회전 후 이전 키는 즉시 폐기됩니다. 진행 중 호출이 끊길 수 있어요.",
    confirmation: "ROTATE",
    confirmLabel: "회전",
    onConfirm: async () => {
      await new Promise((r) => setTimeout(r, 800));
    },
  },
};

export const DeleteCronJob: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "Cron 작업을 삭제할까요?",
    description: "되돌릴 수 없어요.",
    confirmation: "DELETE",
    confirmLabel: "삭제",
    onConfirm: async () => {
      await new Promise((r) => setTimeout(r, 600));
    },
  },
};

export const Pending: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "처리 중",
    description: "외부 mutation 훅과 결합한 상태.",
    confirmation: "ROTATE",
    confirmLabel: "회전",
    isPending: true,
    onConfirm: () => {},
  },
};
