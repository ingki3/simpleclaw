import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Drawer } from "./Drawer";
import { Button } from "@/components/atoms/Button";

const meta: Meta<typeof Drawer> = {
  title: "Primitives/Drawer",
  component: Drawer,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof Drawer>;

function Demo({ args }: { args: Partial<React.ComponentProps<typeof Drawer>> }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="grid min-h-screen place-items-center bg-[--background] p-10">
      <Button onClick={() => setOpen(true)}>Drawer 열기</Button>
      <Drawer
        {...(args as React.ComponentProps<typeof Drawer>)}
        open={open}
        onOpenChange={setOpen}
        footer={
          <Button size="sm" onClick={() => setOpen(false)}>
            닫기
          </Button>
        }
      >
        <p className="text-sm text-[--foreground]">
          시크릿/감사/트레이스 같은 *보조 컨텐츠*를 메인을 가리지 않고 띄울 때 사용합니다.
        </p>
        <ul className="mt-4 space-y-2 text-sm text-[--muted-foreground]">
          <li>· 변경 시각: 12:34:56</li>
          <li>· trace_id: 01HW…</li>
          <li>· 영향 모듈: webhook, channels</li>
        </ul>
      </Drawer>
    </div>
  );
}

export const Default: Story = {
  render: (args) => <Demo args={args} />,
  args: { title: "변경 상세", description: "감사 항목을 상세 보기" },
};

export const Small: Story = {
  render: (args) => <Demo args={args} />,
  args: { title: "Small", size: "sm" },
};

export const Large: Story = {
  render: (args) => <Demo args={args} />,
  args: { title: "Large", size: "lg" },
};
