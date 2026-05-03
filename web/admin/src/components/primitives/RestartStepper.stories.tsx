import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { RestartStepper, type RestartStep } from "./RestartStepper";
import { Button } from "@/components/atoms/Button";

const meta: Meta<typeof RestartStepper> = {
  title: "Primitives/RestartStepper",
  component: RestartStepper,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof RestartStepper>;

function Frame({ step, failed }: { step: RestartStep; failed?: boolean }) {
  const [open, setOpen] = useState(true);
  const [s, setS] = useState<RestartStep>(step);
  const labels: Record<RestartStep, string> = {
    pending: "Dry-run 실행",
    "dry-run": "확정",
    confirm: "지금 재시작",
    applying: "적용 중…",
    done: "확인",
  };
  return (
    <div className="grid min-h-screen place-items-center bg-[--background] p-10">
      <Button onClick={() => setOpen(true)}>재시작 모달 열기</Button>
      <RestartStepper
        open={open}
        onOpenChange={setOpen}
        step={s}
        failed={failed}
        advanceLabel={labels[s]}
        onAdvance={() => {
          if (s === "pending") setS("dry-run");
          else if (s === "dry-run") setS("confirm");
          else if (s === "confirm") setS("applying");
          else if (s === "applying") setS("done");
          else setOpen(false);
        }}
      >
        <p className="text-sm text-[--muted-foreground]">
          {s === "pending" && "데몬 재시작이 필요한 변경 3건이 누적됐어요."}
          {s === "dry-run" &&
            "Dry-run이 정상적으로 끝났어요. 영향 모듈: webhook, channels."}
          {s === "confirm" &&
            "지금 재시작하면 진행 중 요청 4건이 끊깁니다. 계속할까요?"}
          {s === "applying" && "헬스 회복을 기다리는 중… (예상 8초)"}
          {s === "done" &&
            (failed
              ? "재시작 후 헬스가 회복되지 않았어요. 롤백을 권장합니다."
              : "재시작이 끝나고 모든 영역이 정상이에요.")}
        </p>
      </RestartStepper>
    </div>
  );
}

export const Pending: Story = {
  render: () => <Frame step="pending" />,
};
export const DryRun: Story = {
  render: () => <Frame step="dry-run" />,
};
export const Confirm: Story = {
  render: () => <Frame step="confirm" />,
};
export const Applying: Story = {
  render: () => <Frame step="applying" />,
};
export const Done: Story = {
  render: () => <Frame step="done" />,
};
export const DoneFailed: Story = {
  render: () => <Frame step="done" failed />,
};
