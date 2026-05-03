/**
 * Admin API 모듈 — 단일 진입점.
 *
 * 영역별 화면은 ``import { fetchAdmin, useAdminQuery, ... } from "@/lib/api"``
 * 형태로만 사용하면 된다. 내부 구조는 변경 가능하므로 *barrel* 경유로만 의존한다.
 */

export {
  fetchAdmin,
  setAdminApiBaseUrl,
  getAdminApiBaseUrl,
  onAdminApiError,
  type FetchAdminInit,
} from "./client";
export {
  AdminApiError,
  classifyStatus,
  type AdminErrorKind,
  type AdminApiErrorDetails,
} from "./errors";
export {
  generateIdempotencyKey,
  shouldAttachIdempotencyKey,
} from "./idempotency";
export { dryRun, type DryRunResult } from "./dry-run";
export {
  useAdminQuery,
  useAdminMutation,
  type AdminMutationArg,
} from "./hooks";
export {
  useUndo,
  registerUndo,
  consumeUndo,
  getUndoSlot,
  UNDO_WINDOW_MS,
  type UndoSlot,
  type UseUndoResult,
} from "./undo";
export type {
  AdminArea,
  AuditEntryDTO,
  ConfigPatchResponse,
  DiskUsageInfo,
  DryRunDiff,
  HealthSnapshot,
  ListSecretsResponse,
  PolicyLevel,
  PolicySummary,
  SearchAuditResponse,
  SecretMeta,
  SystemInfoResponse,
  SystemRestartResponse,
  UndoAuditResponse,
  ValidationErrorBody,
} from "./types";
