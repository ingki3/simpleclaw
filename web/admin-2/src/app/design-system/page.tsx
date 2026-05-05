"use client";

/**
 * /design-system — Admin 2.0 디자인 시스템 프리뷰 페이지 (BIZ-112 DoD).
 *
 * 책임:
 *  - DESIGN.md §3.1 / §3.2 / §3.4 에 박제된 모든 reusable 을 한 페이지에서 노출.
 *  - 라이트/다크 토글로 토큰 swap 결과를 시각 검증.
 *  - 각 컴포넌트의 default/hover/disabled/error 등 변형 상태도 함께 그린다.
 *
 * 본 페이지는 운영자가 실제 사용하는 화면이 아니라 *팀 내부 카탈로그* 다.
 * S2 이후 Storybook 으로 이관할 수 있도록 컴포넌트 group 별로 명확히 구획.
 */

import { useState } from "react";
import {
  Badge,
  Button,
  Checkbox,
  Code,
  IconButton,
  Input,
  Label,
  Radio,
  SecretField,
  Select,
  StatusPill,
  Switch,
  Textarea,
  Tooltip,
} from "@/design/atoms";
import {
  AuditEntry,
  ConfirmGate,
  DryRunCard,
  EmptyState,
  FormRow,
  HealthDot,
  InputGroup,
  MaskedSecretRow,
  MetricCard,
  PolicyChip,
} from "@/design/molecules";
import {
  CronJobRow,
  MemoryClusterMap,
  PersonaEditor,
  TraceTimeline,
  WebhookGuardCard,
} from "@/design/domain";
import { useTheme, type ThemeMode } from "@/design/ThemeProvider";

function ThemeToggle() {
  const { mode, setMode } = useTheme();
  const opts: { value: ThemeMode; label: string }[] = [
    { value: "light", label: "Light" },
    { value: "dark", label: "Dark" },
    { value: "system", label: "System" },
  ];
  return (
    <div
      role="radiogroup"
      aria-label="theme"
      className="inline-flex rounded-(--radius-m) border border-(--border) bg-(--card) p-1"
    >
      {opts.map((o) => {
        const active = mode === o.value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setMode(o.value)}
            className={`rounded-(--radius-sm) px-3 py-1 text-xs ${
              active
                ? "bg-(--primary) text-(--primary-foreground)"
                : "text-(--foreground)"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="flex items-baseline gap-2 text-xl font-semibold text-(--foreground-strong)">
        {title}
        {count !== undefined ? (
          <span className="text-xs font-normal text-(--muted-foreground)">
            ({count})
          </span>
        ) : null}
      </h2>
      <div className="grid gap-6">{children}</div>
    </section>
  );
}

function Group({
  name,
  children,
}: {
  name: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-(--radius-l) border border-(--border) bg-(--card) p-4">
      <div className="mb-3 text-xs uppercase tracking-wide text-(--muted-foreground)">
        {name}
      </div>
      <div className="flex flex-wrap items-start gap-3">{children}</div>
    </div>
  );
}

export default function DesignSystemPage() {
  // 컴포넌트 상태 데모용 로컬 state.
  const [switchOn, setSwitchOn] = useState(true);
  const [check, setCheck] = useState(true);
  const [radio, setRadio] = useState("a");
  const [persona, setPersona] = useState(
    "# AGENT.md\n- 한국어 응답\n- 간결한 톤",
  );
  const [rl, setRl] = useState(60);
  const [bs, setBs] = useState(256);
  const [cc, setCc] = useState(8);

  return (
    <main
      className="mx-auto flex max-w-6xl flex-col gap-10 p-8"
      data-testid="design-system-root"
    >
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-(--foreground-strong)">
            Admin 2.0 — Design System
          </h1>
          <p className="text-sm text-(--muted-foreground)">
            DESIGN.md §3 인벤토리 박제 — Atomic 14 · Molecular 10 · Domain 5.
          </p>
        </div>
        <ThemeToggle />
      </header>

      {/* ─────────────────────── Atomic ─────────────────────── */}
      <Section title="Atomic" count={14}>
        <Group name="Button — primary / secondary / outline / ghost / destructive">
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="destructive">Destructive</Button>
          <Button disabled>Disabled</Button>
        </Group>

        <Group name="Button sizes">
          <Button size="sm">Small</Button>
          <Button size="md">Medium</Button>
          <Button size="lg">Large</Button>
        </Group>

        <Group name="IconButton">
          <IconButton aria-label="search" icon={<span>🔍</span>} />
          <IconButton aria-label="copy" icon={<span>📋</span>} variant="ghost" />
          <IconButton
            aria-label="play"
            icon={<span>▶</span>}
            variant="primary"
            shape="round"
          />
          <IconButton aria-label="pause" icon={<span>⏸</span>} size="sm" />
        </Group>

        <Group name="Input / Textarea / Select">
          <div className="flex w-72 flex-col gap-2">
            <Input placeholder="default" />
            <Input placeholder="error" error />
            <Input placeholder="disabled" disabled />
            <Textarea placeholder="textarea" />
            <Select
              options={[
                { value: "a", label: "Option A" },
                { value: "b", label: "Option B" },
              ]}
            />
          </div>
        </Group>

        <Group name="Switch / Checkbox / Radio / Label">
          <Switch
            checked={switchOn}
            onCheckedChange={setSwitchOn}
            label="auto restart"
          />
          <Switch checked={false} onCheckedChange={() => {}} disabled />
          <Checkbox
            label="Send anonymized telemetry"
            checked={check}
            onChange={(e) => setCheck(e.currentTarget.checked)}
          />
          <Radio
            label="A"
            name="demo-radio"
            value="a"
            checked={radio === "a"}
            onChange={() => setRadio("a")}
          />
          <Radio
            label="B"
            name="demo-radio"
            value="b"
            checked={radio === "b"}
            onChange={() => setRadio("b")}
          />
          <Label htmlFor="lbl-demo" required>
            Provider name
          </Label>
        </Group>

        <Group name="Badge / StatusPill">
          <Badge tone="neutral">neutral</Badge>
          <Badge tone="success">success</Badge>
          <Badge tone="warning">warning</Badge>
          <Badge tone="danger">danger</Badge>
          <Badge tone="info">info</Badge>
          <Badge tone="brand">brand</Badge>
          <StatusPill tone="success">정상</StatusPill>
          <StatusPill tone="warning">주의</StatusPill>
          <StatusPill tone="error">오류</StatusPill>
          <StatusPill tone="info">실행중</StatusPill>
          <StatusPill tone="neutral">대기</StatusPill>
        </Group>

        <Group name="SecretField / Code / Tooltip">
          <SecretField
            maskedPreview="••••1234"
            revealedValue="sk-live-abcdef1234"
            onReveal={() => {}}
            onCopy={() => {}}
            onRotate={() => {}}
          />
          <Code>keyring:claude_api_key</Code>
          <Code block>{`POST /v1/messages\nAuthorization: Bearer …`}</Code>
          <Tooltip content="단축키 ⌘K">
            <Button variant="outline">Hover for tip</Button>
          </Tooltip>
        </Group>
      </Section>

      {/* ─────────────────────── Molecular ─────────────────────── */}
      <Section title="Molecular" count={10}>
        <Group name="InputGroup / FormRow">
          <div className="w-full">
            <InputGroup
              label="Provider name"
              required
              hint="영문/숫자/하이픈만 허용됩니다"
            >
              <Input placeholder="anthropic" />
            </InputGroup>
            <InputGroup
              label="API key"
              error="이 항목은 필수입니다"
              required
            >
              <Input placeholder="sk-..." error />
            </InputGroup>
            <FormRow
              name={
                <span>
                  Rate limit{" "}
                  <span className="text-xs text-(--muted-foreground)">
                    분당 요청 수
                  </span>
                </span>
              }
              value={
                <div className="flex items-center gap-2">
                  <Input className="w-32" defaultValue="60" />
                  <PolicyChip kind="hot" />
                </div>
              }
            />
          </div>
        </Group>

        <Group name="PolicyChip">
          <PolicyChip kind="hot" />
          <PolicyChip kind="service-restart" />
          <PolicyChip kind="process-restart" meta="(~12s)" />
        </Group>

        <Group name="DryRunCard">
          <DryRunCard
            before={<span>60 req/min</span>}
            after={<span>30 req/min</span>}
            impact="최근 1시간 트래픽 중 12건이 새 임계치에서 차단됩니다."
            onApply={() => {}}
            onCancel={() => {}}
          />
        </Group>

        <Group name="AuditEntry">
          <div className="w-full">
            <AuditEntry
              actor="ingki3"
              action="config.update"
              target={
                <code className="font-mono">
                  llm.providers.claude.model
                </code>
              }
              outcome="applied"
              traceId="01HW1ABCDEF"
              timestamp="23:30"
            />
            <AuditEntry
              actor="ingki3"
              action="secret.rotate"
              target={<code className="font-mono">keyring:openai_api_key</code>}
              outcome="rolled-back"
              traceId="01HW1ZYXWVU"
              timestamp="22:15"
            />
            <AuditEntry
              actor="cron"
              action="recipe.run"
              target="dreaming-pipeline"
              outcome="failed"
              timestamp="20:00"
            />
          </div>
        </Group>

        <Group name="HealthDot / MetricCard">
          <HealthDot tone="green" label="bot up" />
          <HealthDot tone="amber" label="degraded" />
          <HealthDot tone="red" label="down" />
          <HealthDot tone="grey" label="unknown" />
          <MetricCard label="Calls / min" value="42" delta={+8} />
          <MetricCard
            label="Token cost"
            value="$1.20"
            delta={-0.15}
            sparkline={
              <div className="h-6 w-32 rounded-(--radius-sm) bg-(--surface)" />
            }
          />
          <MetricCard label="Errors" value="0" delta="—" deltaTone="neutral" />
        </Group>

        <Group name="EmptyState">
          <EmptyState
            title="등록된 시크릿이 없습니다"
            description="Provider 를 추가하면 API key 가 keyring 에 저장됩니다."
            action={<Button>새 Provider 추가</Button>}
          />
        </Group>

        <Group name="ConfirmGate">
          <ConfirmGate
            keyword="rotate"
            description="OpenAI API 키를 회전합니다. 이 작업은 되돌릴 수 없습니다."
            onConfirm={() => {}}
            onCancel={() => {}}
            countdownSeconds={3}
          />
        </Group>

        <Group name="MaskedSecretRow">
          <div className="w-full">
            <MaskedSecretRow
              keyName="keyring:claude_api_key"
              maskedPreview="••••a8f2"
              revealedValue="sk-ant-…demo"
              onReveal={() => {}}
              onCopy={() => {}}
              onRotate={() => {}}
              meta="last rotated 14d ago"
            />
            <MaskedSecretRow
              keyName="keyring:openai_api_key"
              maskedPreview="••••0c91"
              onReveal={() => {}}
              onCopy={() => {}}
              meta="never rotated"
            />
          </div>
        </Group>
      </Section>

      {/* ─────────────────────── Domain ─────────────────────── */}
      <Section title="Domain" count={5}>
        <Group name="CronJobRow">
          <table className="w-full">
            <thead>
              <tr className="text-left text-xs uppercase text-(--muted-foreground)">
                <th className="px-3 py-2">이름</th>
                <th className="px-3 py-2">스케줄</th>
                <th className="px-3 py-2">다음 실행</th>
                <th className="px-3 py-2">상태</th>
                <th className="px-3 py-2">Circuit</th>
                <th className="px-3 py-2 text-right">액션</th>
              </tr>
            </thead>
            <tbody>
              <CronJobRow
                name="dreaming-pipeline"
                schedule="0 4 * * *"
                nextRun="04:00 (5h)"
                status="success"
                circuit="closed"
                actions={
                  <Button size="sm" variant="ghost">
                    실행
                  </Button>
                }
              />
              <CronJobRow
                name="memory-export"
                schedule="*/30 * * * *"
                nextRun="00:30"
                status="running"
                circuit="half-open"
              />
              <CronJobRow
                name="webhook-pump"
                schedule="* * * * *"
                nextRun="—"
                status="failed"
                circuit="open"
                actions={
                  <Button size="sm" variant="destructive">
                    리셋
                  </Button>
                }
              />
            </tbody>
          </table>
        </Group>

        <Group name="PersonaEditor">
          <PersonaEditor
            value={persona}
            onChange={setPersona}
            tokensCurrent={1240}
            tokensBudget={4000}
            meta="last edit · 2m ago"
          />
        </Group>

        <Group name="WebhookGuardCard">
          <WebhookGuardCard
            rateLimit={{
              label: "Rate limit",
              value: rl,
              min: 0,
              max: 240,
              unit: "req/min",
              onChange: setRl,
            }}
            bodySize={{
              label: "Body size",
              value: bs,
              min: 16,
              max: 1024,
              step: 16,
              unit: "KB",
              onChange: setBs,
            }}
            concurrency={{
              label: "Concurrency",
              value: cc,
              min: 1,
              max: 32,
              onChange: setCc,
            }}
            simulation="현재 임계치로 1시간 시뮬: 허용 3,201 · 차단 12."
          />
        </Group>

        <Group name="TraceTimeline">
          <TraceTimeline
            spans={[
              { id: "1", name: "router.send", startMs: 0, endMs: 800, tone: "primary" },
              { id: "2", name: "claude.api", startMs: 60, endMs: 720, tone: "success" },
              { id: "3", name: "tool.search", startMs: 740, endMs: 900, tone: "warning" },
              { id: "4", name: "memory.write", startMs: 900, endMs: 980, tone: "muted" },
            ]}
            totalMs={1000}
          />
        </Group>

        <Group name="MemoryClusterMap">
          <MemoryClusterMap
            clusters={[
              {
                id: "a",
                label: "코드 리뷰",
                count: 124,
                tone: "primary",
                keywords: ["typescript", "react", "test"],
              },
              {
                id: "b",
                label: "운영",
                count: 78,
                tone: "info",
                keywords: ["배포", "incident"],
              },
              {
                id: "c",
                label: "메모",
                count: 42,
                tone: "success",
                keywords: ["프로젝트", "회고"],
              },
              {
                id: "d",
                label: "기타",
                count: 21,
                tone: "warning",
              },
            ]}
          />
        </Group>
      </Section>
    </main>
  );
}
