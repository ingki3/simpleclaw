/**
 * Admin API 에러 정규화 — fetchAdmin이 던지는 단일 에러 타입.
 *
 * 화면 코드가 status별 분기를 ``error.status === 401``처럼 쉽게 작성할 수 있도록
 * Error 클래스 하나로 통일한다. 422 검증 에러는 ``details.errors`` 배열을 보존.
 */

export type AdminErrorKind =
  | "unauthorized" //  401
  | "forbidden" //     403
  | "not_found" //     404
  | "conflict" //      409
  | "validation" //    422
  | "server" //        5xx
  | "network" //       네트워크/타임아웃
  | "unknown";

export interface AdminApiErrorDetails {
  /** 422 응답 본문의 ``errors`` 필드. */
  errors?: string[];
  /** 응답 본문 전체 — 디버깅용. */
  body?: unknown;
}

export class AdminApiError extends Error {
  /** HTTP status. 네트워크 실패 시 0. */
  readonly status: number;
  /** 분류 — 화면에서 status보다 ``kind``로 분기하는 게 안전. */
  readonly kind: AdminErrorKind;
  readonly details: AdminApiErrorDetails;

  constructor(
    message: string,
    status: number,
    kind: AdminErrorKind,
    details: AdminApiErrorDetails = {},
  ) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
    this.kind = kind;
    this.details = details;
  }
}

/** status 숫자를 사람이 읽기 좋은 메시지 + 분류로 변환. */
export function classifyStatus(status: number): {
  kind: AdminErrorKind;
  fallbackMessage: string;
} {
  if (status === 401) {
    return {
      kind: "unauthorized",
      fallbackMessage: "Admin 토큰이 유효하지 않아요. 다시 확인해 주세요.",
    };
  }
  if (status === 403) {
    return {
      kind: "forbidden",
      fallbackMessage: "이 작업을 수행할 권한이 없어요.",
    };
  }
  if (status === 404) {
    return {
      kind: "not_found",
      fallbackMessage: "요청한 리소스를 찾을 수 없어요.",
    };
  }
  if (status === 409) {
    return {
      kind: "conflict",
      fallbackMessage: "현재 상태와 충돌해 작업이 거부됐어요.",
    };
  }
  if (status === 422) {
    return {
      kind: "validation",
      fallbackMessage: "입력값이 유효하지 않아요. 자세한 사유를 확인해 주세요.",
    };
  }
  if (status >= 500 && status < 600) {
    return {
      kind: "server",
      fallbackMessage:
        "데몬에서 오류가 발생했어요. 잠시 뒤 다시 시도하거나 로그를 확인해 주세요.",
    };
  }
  return {
    kind: "unknown",
    fallbackMessage: "알 수 없는 오류가 발생했어요.",
  };
}
