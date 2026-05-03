import type { Meta, StoryObj } from "@storybook/react";
import { ToastProvider, useToast } from "./Toast";
import { Button } from "@/components/atoms/Button";

const meta: Meta = {
  title: "Primitives/Toast",
  parameters: { layout: "fullscreen" },
};
export default meta;

function Demo({ kind }: { kind: "success" | "info" | "warn" | "destructive-soft" | "undo" }) {
  const { push } = useToast();
  const fire = () => {
    if (kind === "undo") {
      push({
        tone: "success",
        title: "변경이 적용됐어요.",
        description: "5분 안에 되돌릴 수 있어요.",
        undo: {
          onUndo: async () => {
            await new Promise((r) => setTimeout(r, 600));
          },
          expiresAt: Date.now() + 60_000,
          label: "되돌리기",
        },
      });
      return;
    }
    push({
      tone: kind,
      title:
        kind === "success"
          ? "Webhook 설정이 적용됐어요."
          : kind === "info"
            ? "백업 파일이 생성됐어요."
            : kind === "warn"
              ? "재시도가 임계치에 가까워요."
              : "Telegram API에 연결되지 않았어요.",
      description:
        kind === "destructive-soft"
          ? "토큰을 다시 확인하거나 재시도를 눌러 주세요."
          : undefined,
    });
  };
  return (
    <div className="grid min-h-screen place-items-center bg-[--background]">
      <Button onClick={fire}>토스트 띄우기 ({kind})</Button>
    </div>
  );
}

type Story = StoryObj<typeof Demo>;

const wrap = (k: Parameters<typeof Demo>[0]["kind"]): Story => ({
  render: () => (
    <ToastProvider>
      <Demo kind={k} />
    </ToastProvider>
  ),
});

export const Success = wrap("success");
export const Info = wrap("info");
export const Warn = wrap("warn");
export const DestructiveSoft = wrap("destructive-soft");
export const WithUndo = wrap("undo");
