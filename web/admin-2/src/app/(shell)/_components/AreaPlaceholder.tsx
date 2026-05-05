/**
 * AreaPlaceholder — 11개 영역 라우트의 임시 콘텐츠 (BIZ-113).
 *
 * S3~S13 sub-issue 가 각 영역의 진짜 콘텐츠를 채울 때 본 placeholder 는 교체된다.
 * 본 단계의 책임:
 *  - 운영자가 라우트 셸이 동작함을 시각적으로 확인할 수 있게 한다.
 *  - 영역 메타(label/description) 를 SSOT(`AREAS`) 그대로 노출.
 *  - placeholder 임을 명확히 알려 후속 sub-issue 가 어디를 손대야 하는지 보여준다.
 */
import { findAreaByPath } from "@/app/areas";
import { StatusPill } from "@/design/atoms/StatusPill";

interface AreaPlaceholderProps {
  path: string;
  /** sub-issue 번호 — 예: "BIZ-114" — 운영자에게 다음 작업 이슈를 명시. */
  upcomingIssue?: string;
}

export function AreaPlaceholder({ path, upcomingIssue }: AreaPlaceholderProps) {
  const area = findAreaByPath(path);
  if (!area) {
    return (
      <section className="flex flex-col gap-2 p-8" data-testid="area-placeholder">
        <h1 className="text-2xl font-semibold text-(--foreground-strong)">
          알 수 없는 영역
        </h1>
        <p className="text-sm text-(--muted-foreground)">{path}</p>
      </section>
    );
  }

  return (
    <section
      className="mx-auto flex max-w-4xl flex-col gap-6 p-8"
      data-testid="area-placeholder"
      data-area-path={area.path}
    >
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold text-(--foreground-strong)">
            {area.label}
          </h1>
          <StatusPill tone="info">S2 placeholder</StatusPill>
        </div>
        <p className="text-sm text-(--muted-foreground)">{area.description}</p>
      </header>

      <div className="rounded-(--radius-l) border border-dashed border-(--border-strong) bg-(--card) p-8">
        <p className="text-sm text-(--foreground)">
          이 영역의 콘텐츠는 후속 sub-issue
          {upcomingIssue ? (
            <>
              {" "}
              <code className="rounded-(--radius-sm) bg-(--surface) px-1.5 py-0.5 font-mono text-xs">
                {upcomingIssue}
              </code>
              {" "}
            </>
          ) : (
            " "
          )}
          에서 채워집니다.
        </p>
        <p className="mt-2 text-xs text-(--muted-foreground)">
          현재는 App Shell (BIZ-113) 의 라우트 셸만 동작합니다 — Sidebar nav,
          Topbar breadcrumb, ⌘K Command Palette 가 모든 영역에서 동일하게 떠야
          합니다.
        </p>
      </div>
    </section>
  );
}
