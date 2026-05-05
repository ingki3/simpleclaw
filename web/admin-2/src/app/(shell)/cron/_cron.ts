/**
 * Cron 표현식 파서 + 다음 실행 시각 계산기 — S7 (BIZ-118).
 *
 * admin.pen `Y0X0SZ` 의 "Cron 표현식" 입력 필드와 DryRunCard (다음 N회 실행
 * 미리보기) 가 본 모듈의 단일 책임이다. 외부 라이브러리 의존을 피해 다음
 * 두 표기만 박제한다:
 *
 *   1) 표준 5-필드 crontab:  `m h dom mon dow`
 *      - 필드 토큰: `*`, `*\/N`, `N`, `N-M`, `N,M`, `N-M\/S`
 *      - dow: 0=일..6=토 (7=일 도 허용), 영문 약어 SUN/MON/TUE/.../SAT 허용
 *      - mon: 1=1월..12=12월, 영문 약어 JAN/FEB/.../DEC 허용
 *
 *   2) 친화 표기 `every Nm` / `every Nh` / `every Nd`
 *      - admin.pen `dreaming.cycle` 행처럼 사람이 읽기 쉬운 표기.
 *      - 내부적으로 5-필드로 정규화 후 동일 엔진으로 처리한다.
 *
 * 본 단계는 데몬 미연결 — 다음 실행 시각은 클라이언트에서 그려 미리보기 한다.
 * 검증 통과만 보장하면 데몬이 실제 발화 시각을 계산한다.
 *
 * 주의: 본 파서는 admin UI 미리보기 용도다. 데몬은 별도 라이브러리(croniter 등)
 * 로 정밀 계산한다 — UI 미리보기와 데몬이 1초 단위까지 일치할 필요는 없다.
 */

const DOW_ALIAS: Record<string, number> = {
  SUN: 0,
  MON: 1,
  TUE: 2,
  WED: 3,
  THU: 4,
  FRI: 5,
  SAT: 6,
};

const MON_ALIAS: Record<string, number> = {
  JAN: 1,
  FEB: 2,
  MAR: 3,
  APR: 4,
  MAY: 5,
  JUN: 6,
  JUL: 7,
  AUG: 8,
  SEP: 9,
  OCT: 10,
  NOV: 11,
  DEC: 12,
};

interface FieldRange {
  min: number;
  max: number;
  /** alias 사전 — 토큰을 정수로 미리 치환할 때 사용. */
  alias?: Record<string, number>;
}

const FIELD_RANGES: readonly FieldRange[] = [
  { min: 0, max: 59 }, // minute
  { min: 0, max: 23 }, // hour
  { min: 1, max: 31 }, // day-of-month
  { min: 1, max: 12, alias: MON_ALIAS },
  { min: 0, max: 6, alias: DOW_ALIAS },
];

export interface CronParseSuccess {
  ok: true;
  /** 정규화된 표준 5-필드 표현식 — 친화 표기 입력은 여기서 표준으로 환산. */
  normalized: string;
  /** 사람 친화 한 줄 요약 — 미리보기 카드의 "After" 행에 노출. */
  description: string;
  /** 각 필드별 허용된 정수 집합 — 테스트 가독성을 위해 노출. */
  fields: readonly number[][];
}

export interface CronParseError {
  ok: false;
  /** 한국어 한 줄 — 폼 에러 라인에 그대로 노출. */
  message: string;
}

export type CronParseResult = CronParseSuccess | CronParseError;

/**
 * 표현식을 파싱·검증한다. 입력은 표준 5-필드 또는 `every Nm/Nh/Nd` 친화 표기.
 * 친화 표기는 표준 표현으로 정규화되어 `normalized` 로 반환된다.
 */
export function parseCron(expression: string): CronParseResult {
  const raw = expression.trim();
  if (!raw) {
    return { ok: false, message: "Cron 표현식을 입력하세요." };
  }
  // 친화 표기 → 표준 표현으로 환산.
  const normalized = expandFriendly(raw);
  const tokens = normalized.split(/\s+/);
  if (tokens.length !== 5) {
    return {
      ok: false,
      message: "5개 필드가 필요합니다 (분 시 일 월 요일).",
    };
  }

  const fields: number[][] = [];
  for (let i = 0; i < 5; i++) {
    const token = tokens[i] ?? "*";
    const range = FIELD_RANGES[i]!;
    const parsed = parseField(token, range);
    if (!parsed.ok) {
      return {
        ok: false,
        message: `${FIELD_NAME[i]} 필드 — ${parsed.message}`,
      };
    }
    fields.push(parsed.values);
  }

  return {
    ok: true,
    normalized,
    description: describe(normalized, raw),
    fields,
  };
}

/** 친화 표기 (`every Nm/Nh/Nd`) → 5-필드 정규화. 표준 입력은 그대로 반환. */
export function expandFriendly(input: string): string {
  const trimmed = input.trim();
  const m = /^every\s+(\d+)\s*(m|h|d)$/i.exec(trimmed);
  if (!m) return trimmed;
  const n = Number(m[1]);
  const unit = (m[2] ?? "").toLowerCase();
  if (!Number.isFinite(n) || n <= 0) return trimmed;
  if (unit === "m") {
    if (n >= 60) return trimmed; // 60분 이상은 시간 단위로 입력 권장 — 그대로 두면 검증 실패.
    return `*/${n} * * * *`;
  }
  if (unit === "h") {
    if (n >= 24) return trimmed;
    return `0 */${n} * * *`;
  }
  if (unit === "d") {
    if (n >= 32) return trimmed;
    return `0 0 */${n} * *`;
  }
  return trimmed;
}

const FIELD_NAME = ["분", "시", "일", "월", "요일"] as const;

interface FieldParseSuccess {
  ok: true;
  values: number[];
}

interface FieldParseError {
  ok: false;
  message: string;
}

type FieldParseResult = FieldParseSuccess | FieldParseError;

function parseField(token: string, range: FieldRange): FieldParseResult {
  if (!token) return { ok: false, message: "빈 토큰입니다." };
  // 콤마 분리 — 각 부분을 개별 파싱 후 합집합.
  const parts = token.split(",");
  const set = new Set<number>();
  for (const part of parts) {
    const sub = parseSubField(part, range);
    if (!sub.ok) return sub;
    for (const v of sub.values) set.add(v);
  }
  return { ok: true, values: [...set].sort((a, b) => a - b) };
}

function parseSubField(part: string, range: FieldRange): FieldParseResult {
  const token = part.trim();
  if (!token) return { ok: false, message: "빈 토큰입니다." };

  let stepRaw: string | undefined;
  let baseRaw = token;
  if (token.includes("/")) {
    const [b, s] = token.split("/", 2);
    baseRaw = (b ?? "").trim();
    stepRaw = (s ?? "").trim();
  }
  const step = stepRaw === undefined ? 1 : Number(stepRaw);
  if (!Number.isFinite(step) || step < 1 || !Number.isInteger(step)) {
    return { ok: false, message: `step '${stepRaw}' 가 유효하지 않습니다.` };
  }

  let lo: number;
  let hi: number;
  if (baseRaw === "*" || baseRaw === "") {
    lo = range.min;
    hi = range.max;
  } else if (baseRaw.includes("-")) {
    const [a, b] = baseRaw.split("-", 2);
    const av = resolveValue(a ?? "", range);
    const bv = resolveValue(b ?? "", range);
    if (av === null || bv === null) {
      return { ok: false, message: `범위 '${baseRaw}' 가 유효하지 않습니다.` };
    }
    if (av > bv) {
      return { ok: false, message: `범위 '${baseRaw}' 는 시작이 끝보다 작아야 합니다.` };
    }
    lo = av;
    hi = bv;
  } else {
    const v = resolveValue(baseRaw, range);
    if (v === null) {
      return { ok: false, message: `토큰 '${baseRaw}' 가 유효하지 않습니다.` };
    }
    if (v < range.min || v > range.max) {
      return {
        ok: false,
        message: `값이 ${range.min}~${range.max} 범위 밖입니다.`,
      };
    }
    if (stepRaw !== undefined) {
      // `N/S` — N 부터 max 까지 step.
      lo = v;
      hi = range.max;
    } else {
      return { ok: true, values: [v] };
    }
  }
  if (lo < range.min || hi > range.max) {
    return {
      ok: false,
      message: `값이 ${range.min}~${range.max} 범위 밖입니다.`,
    };
  }
  const out: number[] = [];
  for (let v = lo; v <= hi; v += step) out.push(v);
  return { ok: true, values: out };
}

function resolveValue(token: string, range: FieldRange): number | null {
  const upper = token.toUpperCase();
  if (range.alias && upper in range.alias) {
    return range.alias[upper] ?? null;
  }
  // dow 의 7 = 일요일 별칭 처리 — 편의를 위해 공식 spec 과 정렬.
  if (range === FIELD_RANGES[4] && token === "7") return 0;
  const n = Number(token);
  if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
  return n;
}

/**
 * 정규화된 표현으로부터 다음 `count` 개 실행 시각을 계산한다.
 * `from` 기준 그 이후로 발화하는 시각을 분 단위로 탐색한다.
 *
 * 안전장치 — 최대 366일까지만 탐색하고, 그래도 발화하지 않으면 빈 배열.
 */
export function nextRuns(
  parsed: CronParseSuccess,
  from: Date,
  count: number,
): Date[] {
  const [minutes, hours, days, months, dows] = parsed.fields;
  if (!minutes || !hours || !days || !months || !dows) return [];
  // 분 단위 탐색 — Date 의 setSeconds/setMilliseconds 를 0 으로 고정.
  const cursor = new Date(from.getTime());
  cursor.setSeconds(0, 0);
  // 다음 분부터 시작 — 현재 분과 동시 발화는 미리보기에서 제외.
  cursor.setMinutes(cursor.getMinutes() + 1);

  const results: Date[] = [];
  const limit = 366 * 24 * 60; // 1년 = 분 단위 상한.
  for (let i = 0; i < limit && results.length < count; i++) {
    if (
      minutes.includes(cursor.getMinutes()) &&
      hours.includes(cursor.getHours()) &&
      days.includes(cursor.getDate()) &&
      months.includes(cursor.getMonth() + 1) &&
      dows.includes(cursor.getDay())
    ) {
      results.push(new Date(cursor.getTime()));
    }
    cursor.setMinutes(cursor.getMinutes() + 1);
  }
  return results;
}

/** 친화 한 줄 요약 — 입력이 친화 표기였다면 우선적으로 그대로 표기. */
function describe(normalized: string, original: string): string {
  if (/^every\s+/i.test(original)) return `사람 친화 표기 — ${original}`;
  // 가장 흔한 패턴 몇 가지를 한국어로 풀이 — 그 외는 그대로.
  if (normalized === "* * * * *") return "1분마다 실행";
  const everyMin = /^\*\/(\d+) \* \* \* \*$/.exec(normalized);
  if (everyMin) return `${everyMin[1]}분마다 실행`;
  const everyHour = /^0 \*\/(\d+) \* \* \*$/.exec(normalized);
  if (everyHour) return `${everyHour[1]}시간마다 정각에 실행`;
  const dailyHour = /^0 (\d+) \* \* \*$/.exec(normalized);
  if (dailyHour) return `매일 ${dailyHour[1]}:00 에 실행`;
  return normalized;
}
