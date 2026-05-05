"use client";

/**
 * TrafficSimulationModal — admin.pen `cdHTp` (Traffic Simulation) 박제.
 *
 * Webhook Edit modal 의 footerLeft "트래픽 시뮬레이션" 버튼으로 진입하는
 * *미리보기 전용* 모달. WebhookGuardCard (domain reusable) 의 3개 슬라이더로
 * 입력 부하 (req/s, Burst peak, 동시성) 를 바꾸고, 우측에 결과 미리보기 카드 +
 * 처리/대기/거부 메트릭 박스 3종을 노출.
 *
 * 본 단계는 시뮬레이터가 결정론적 박제 — `simulateTraffic()` 호출이 즉시
 * 결과를 돌려준다. 데몬 통합 단계에서 본 모달이 비동기 fetch 로 교체.
 */

import { useMemo, useState } from "react";
import { Button } from "@/design/atoms/Button";
import { WebhookGuardCard } from "@/design/domain/WebhookGuardCard";
import { cn } from "@/lib/cn";
import { simulateTraffic, type WebhookEndpoint } from "../_data";
import { Modal } from "./Modal";

interface TrafficSimulationModalProps {
  open: boolean;
  /** 시뮬 대상 endpoint — null 이면 모달 닫힌 상태. */
  endpoint: WebhookEndpoint | null;
  onClose: () => void;
}

/** 시뮬 입력 부하 — 모달 내부 state 의 SSOT. */
interface InputLoad {
  reqPerSec: number;
  burstPeak: number;
  concurrency: number;
}

const DEFAULT_LOAD: InputLoad = {
  reqPerSec: 42,
  burstPeak: 180,
  concurrency: 4,
};

export function TrafficSimulationModal({
  open,
  endpoint,
  onClose,
}: TrafficSimulationModalProps) {
  const [load, setLoad] = useState<InputLoad>(DEFAULT_LOAD);

  if (!open || !endpoint) {
    return (
      <Modal open={false} onClose={onClose} title={null} footer={null}>
        {null}
      </Modal>
    );
  }

  return (
    <TrafficSimulationContent
      endpoint={endpoint}
      load={load}
      setLoad={setLoad}
      onClose={onClose}
    />
  );
}

function TrafficSimulationContent({
  endpoint,
  load,
  setLoad,
  onClose,
}: {
  endpoint: WebhookEndpoint;
  load: InputLoad;
  setLoad: (next: InputLoad) => void;
  onClose: () => void;
}) {
  const result = useMemo(
    () =>
      simulateTraffic({
        ...load,
        rateLimitPerSec: endpoint.rateLimitPerSec,
      }),
    [load, endpoint.rateLimitPerSec],
  );

  return (
    <Modal
      open
      onClose={onClose}
      width="lg"
      data-testid="traffic-simulation-modal"
      title={
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-(--primary)">
            ∿
          </span>
          <h2 className="text-lg font-semibold text-(--foreground-strong)">
            트래픽 시뮬레이션 — <span className="font-mono">{endpoint.id}</span>
          </h2>
        </div>
      }
      footer={
        <Button
          variant="secondary"
          onClick={onClose}
          data-testid="traffic-simulation-close"
        >
          닫기
        </Button>
      }
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div data-testid="traffic-simulation-guard">
        <WebhookGuardCard
          rateLimit={{
            label: "req/s",
            value: load.reqPerSec,
            min: 0,
            max: 200,
            unit: "req/s",
            onChange: (next) => setLoad({ ...load, reqPerSec: next }),
          }}
          bodySize={{
            label: "Burst (peak)",
            value: load.burstPeak,
            min: 0,
            max: 500,
            step: 5,
            unit: "req",
            onChange: (next) => setLoad({ ...load, burstPeak: next }),
          }}
          concurrency={{
            label: "동시성",
            value: load.concurrency,
            min: 1,
            max: 32,
            onChange: (next) => setLoad({ ...load, concurrency: next }),
          }}
        />
        </div>

        <section
          data-testid="traffic-simulation-preview"
          className="flex flex-col gap-3 rounded-(--radius-l) border border-(--border) bg-(--card) p-6"
        >
          <header className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-(--foreground-strong)">
              결과 미리보기
            </h3>
            <span className="rounded-(--radius-pill) bg-black px-3 py-1 font-mono text-xs text-white">
              {endpoint.purpose}
            </span>
          </header>
          <div
            data-testid="traffic-simulation-chart"
            className="flex h-40 items-center justify-center rounded-(--radius-m) bg-(--surface) text-xs text-(--muted-foreground)"
          >
            {result.chartLabel}
          </div>
          <div className="grid grid-cols-3 gap-2">
            <MetricBox
              testId="traffic-simulation-served"
              label="처리"
              value={result.served}
              tone="muted"
            />
            <MetricBox
              testId="traffic-simulation-queued"
              label="대기"
              value={result.queued}
              tone="muted"
            />
            <MetricBox
              testId="traffic-simulation-rejected"
              label="거부"
              value={result.rejected}
              tone="error"
            />
          </div>
        </section>
      </div>
    </Modal>
  );
}

/** 처리/대기/거부 메트릭 박스 — 검은 카드 + 큰 % 텍스트. */
function MetricBox({
  testId,
  label,
  value,
  tone,
}: {
  testId: string;
  label: string;
  value: number;
  tone: "muted" | "error";
}) {
  const pct = `${Math.round(value * 100)}%`;
  return (
    <div
      data-testid={testId}
      className="flex h-24 flex-col justify-between rounded-(--radius-m) bg-black p-3 text-white"
    >
      <span className="text-xs text-white/70">{label}</span>
      <span
        className={cn(
          "text-xl font-semibold tabular-nums",
          tone === "error" ? "text-(--color-error)" : "text-white",
        )}
      >
        {pct}
      </span>
    </div>
  );
}
