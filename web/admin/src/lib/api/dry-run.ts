/**
 * Dry-run 헬퍼 — 변경 적용 전 diff/policy 미리보기.
 *
 * BIZ-41 백엔드는 ``PATCH /config/{area}?dry_run=true``에 대해 다음 형태의 응답을 준다:
 *
 *  {
 *    "outcome": "dry_run",
 *    "diff": { "before": {...}, "after": {...} },
 *    "policy": { "level": "hot|service_restart|process_restart", ... }
 *  }
 *
 * UI는 응답 그대로 ``DryRunCard``에 전달해 before/after 패널과 정책 라벨을 그린다.
 * 본 헬퍼는 dry_run 외 응답이 오면 명시적으로 거부한다 — 호출자가 우연히
 * 실제 변경을 적용해 버리는 사고를 막기 위함.
 */

import { fetchAdmin, type FetchAdminInit } from "./client";
import { AdminApiError } from "./errors";
import type { ConfigPatchResponse, DryRunDiff, PolicySummary } from "./types";

export interface DryRunResult {
  diff: DryRunDiff;
  policy: PolicySummary;
}

/**
 * ``PATCH /config/{area}``의 dry-run 모드를 호출한다.
 *
 * @param area  ``llm`` / ``webhook`` 등 admin_api.py의 AREA_TO_YAML_KEY 키.
 * @param patch 적용 후보 패치 — 부분 dict.
 * @param init  추가 fetch 옵션 (signal/idempotencyKey 등).
 */
export async function dryRun(
  area: string,
  patch: Record<string, unknown>,
  init: FetchAdminInit = {},
): Promise<DryRunResult> {
  const path = `/config/${encodeURIComponent(area)}?dry_run=true`;
  const response = await fetchAdmin<ConfigPatchResponse>(path, {
    ...init,
    method: "PATCH",
    json: patch,
  });
  if (!response || response.outcome !== "dry_run" || !response.diff) {
    // 백엔드가 우연히 실제 적용해 버린 결과를 받았다면 호출자가 이를 인지하도록
    // 의도적으로 에러를 던진다 — 응답은 details.body로 보존.
    throw new AdminApiError(
      "Dry-run 응답 형식이 예상과 달라요. 데몬 버전을 확인해 주세요.",
      200,
      "unknown",
      { body: response },
    );
  }
  return { diff: response.diff, policy: response.policy };
}
