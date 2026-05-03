import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Modal } from "./Modal";
import { Button } from "@/components/atoms/Button";

const meta: Meta<typeof Modal> = {
  title: "Primitives/Modal",
  component: Modal,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof Modal>;

function Demo({ args }: { args: Partial<React.ComponentProps<typeof Modal>> }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="grid min-h-screen place-items-center bg-[--background] p-10">
      <Button onClick={() => setOpen(true)}>모달 열기</Button>
      <Modal
        {...(args as React.ComponentProps<typeof Modal>)}
        open={open}
        onOpenChange={setOpen}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button size="sm" onClick={() => setOpen(false)}>
              확인
            </Button>
          </>
        }
      >
        <p>
          이 변경은 데몬을 재시작합니다. 진행 중인 요청 4건이 끊깁니다.
        </p>
      </Modal>
    </div>
  );
}

export const Default: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "변경을 적용할까요?",
    description: "Hot 영역이 아니므로 데몬 재시작이 필요해요.",
    size: "md",
  },
};

export const Small: Story = {
  render: (args) => <Demo args={args} />,
  args: { title: "작은 모달", size: "sm" },
};

export const Large: Story = {
  render: (args) => <Demo args={args} />,
  args: { title: "큰 모달", size: "lg" },
};

export const Alert: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "위험한 변경입니다",
    description: "되돌릴 수 없어요. 신중하게 결정해 주세요.",
    alert: true,
  },
};

export const NotDismissible: Story = {
  render: (args) => <Demo args={args} />,
  args: {
    title: "진행 중",
    description: "ESC/바깥 클릭으로 닫을 수 없어요.",
    dismissible: false,
  },
};
