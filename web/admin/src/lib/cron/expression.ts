/**
 * Cron 표현식 검증 + 다음 실행 시각 계산.
 *
 * 백엔드는 APScheduler의 ``CronTrigger``로 표준 5필드 표현식
 * (``min hour dom mon dow``)을 사용하므로, UI 검증 기준도 동일 문법으로 맞춘다.
 * 서버 검증(`/admin/v1/cron/preview`)이 최종 진실이지만, 클라이언트 검증을
 * 동일 규칙으로 두면 키 입력 즉시 인라인 에러를 노출할 수 있다.
 *
 * 본 모듈은 외부 cron 라이브러리에 의존하지 않는다 — 5필드 + ``* / , -``
 * 문법만 다루므로 직접 파싱하는 비용이 더 작다. step 값은 양의 정수만 허용하고,
 * 요일은 ``0=일`` 컨벤션(APScheduler 기본)을 따른다.
 */

export type CronValidationResult =
  | { valid: true; description: string }
  | { valid: false; error: string };

interface FieldSpec {
  name: string;
  /** 합법 범위(폐구간). */
  min: number;
  max: number;
  /** 한국어 라벨 — 에러 메시지에 사용. */
  label: string;
}

const FIELDS: readonly FieldSpec[] = [
  { name: "minute", min: 0, max: 59, label: "분" },
  { name: "hour", min: 0, max: 23, label: "시" },
  { name: "dayOfMonth", min: 1, max: 31, label: "일" },
  { name: "month", min: 1, max: 12, label: "월" },
  // APScheduler: 0=일(Sunday), 6=토. 7도 일요일로 받지만 정규화는 하지 않는다.
  { name: "dayOfWeek", min: 0, max: 7, label: "요일" },
] as const;

const DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"];

const MONTH_ALIASES: Record<string, number> = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
};

const DOW_ALIASES: Record<string, number> = {
  sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6,
};

/**
 * 표현식의 단일 필드를 검증한다. 잘못된 토큰을 만나면 바로 에러를 던진다.
 *
 * 반환값은 매칭되는 정수 집합. 너무 큰 집합(예: ``*``)은 ``"all"`` 으로 표시해
 * 발신측이 굳이 60개짜리 배열을 들고 다니지 않게 한다.
 */
function parseField(token: string, spec: FieldSpec): Set<number> | "all" {
  const lower = token.toLowerCase().trim();
  if (!lower) {
    throw new Error(`${spec.label} 필드가 비어 있어요.`);
  }

  // ``*`` — 전체 범위. step이 붙으면 ``*/n`` 형태로 별도 처리.
  if (lower === "*") return "all";

  const result = new Set<number>();

  for (const part of lower.split(",")) {
    const [rangePart, stepPart] = part.split("/");
    let step = 1;
    if (stepPart !== undefined) {
      const parsed = Number(stepPart);
      if (!Number.isInteger(parsed) || parsed <= 0) {
        throw new Error(`${spec.label} step은 양의 정수여야 해요 — '${part}'.`);
      }
      step = parsed;
    }

    let start: number;
    let end: number;
    if (rangePart === "*") {
      start = spec.min;
      end = spec.max;
    } else if (rangePart.includes("-")) {
      const [a, b] = rangePart.split("-");
      start = parseSingle(a, spec);
      end = parseSingle(b, spec);
      if (start > end) {
        throw new Error(`${spec.label} 범위는 작은 값부터 적어야 해요 — '${part}'.`);
      }
    } else {
      const v = parseSingle(rangePart, spec);
      // step이 있으면 ``v/step`` = ``v-max/step``으로 해석(crontab 관습).
      start = v;
      end = stepPart !== undefined ? spec.max : v;
    }

    for (let i = start; i <= end; i += step) result.add(i);
  }

  if (result.size === 0) {
    throw new Error(`${spec.label} 필드가 매칭되는 값을 만들지 못했어요.`);
  }
  return result;
}

function parseSingle(token: string, spec: FieldSpec): number {
  const lower = token.toLowerCase();
  if (spec.name === "month" && MONTH_ALIASES[lower] !== undefined) {
    return MONTH_ALIASES[lower];
  }
  if (spec.name === "dayOfWeek" && DOW_ALIASES[lower] !== undefined) {
    return DOW_ALIASES[lower];
  }
  const n = Number(lower);
  if (!Number.isInteger(n)) {
    throw new Error(`${spec.label} 값 '${token}'은(는) 정수가 아니에요.`);
  }
  if (n < spec.min || n > spec.max) {
    throw new Error(
      `${spec.label} 값 ${n}은(는) 범위 ${spec.min}–${spec.max}을(를) 벗어났어요.`,
    );
  }
  return n;
}

interface ParsedCron {
  minute: Set<number> | "all";
  hour: Set<number> | "all";
  dayOfMonth: Set<number> | "all";
  month: Set<number> | "all";
  dayOfWeek: Set<number> | "all";
}

function parseCron(expr: string): ParsedCron {
  const tokens = expr.trim().split(/\s+/);
  if (tokens.length !== 5) {
    throw new Error(
      `5개 필드(분 시 일 월 요일)가 필요해요. 현재 ${tokens.length}개.`,
    );
  }
  const [m, h, dom, mon, dow] = tokens;
  return {
    minute: parseField(m, FIELDS[0]),
    hour: parseField(h, FIELDS[1]),
    dayOfMonth: parseField(dom, FIELDS[2]),
    month: parseField(mon, FIELDS[3]),
    dayOfWeek: parseField(dow, FIELDS[4]),
  };
}

/**
 * 사람이 읽을 한 줄 요약 — 자주 쓰는 패턴 우선, 그 외는 토큰을 그대로 노출.
 *
 * 정확한 문장 생성은 cron-parser 같은 라이브러리의 영역이지만, 운영자가 익숙한
 * 패턴(``매시 정각``, ``매일 09:00``)만 짧게 안내해도 입력 검증의 신호로
 * 충분하다. 읽기 어려운 경우는 "표현식이 유효해요"로 폴백한다.
 */
function describe(expr: string, parsed: ParsedCron): string {
  const tokens = expr.trim().split(/\s+/);
  const [m, h, dom, mon, dow] = tokens;

  // ``* * * * *``
  if (m === "*" && h === "*" && dom === "*" && mon === "*" && dow === "*") {
    return "매분 실행";
  }
  // ``0 * * * *``
  if (m === "0" && h === "*" && dom === "*" && mon === "*" && dow === "*") {
    return "매시 정각 실행";
  }
  // ``M H * * *`` — 매일 H:M
  if (
    parsed.minute !== "all" &&
    parsed.hour !== "all" &&
    parsed.dayOfMonth === "all" &&
    parsed.month === "all" &&
    parsed.dayOfWeek === "all" &&
    (parsed.minute as Set<number>).size === 1 &&
    (parsed.hour as Set<number>).size === 1
  ) {
    const hh = [...(parsed.hour as Set<number>)][0];
    const mm = [...(parsed.minute as Set<number>)][0];
    return `매일 ${pad2(hh)}:${pad2(mm)}`;
  }
  // ``M H * * D`` — 매주 D요일 H:M
  if (
    parsed.dayOfMonth === "all" &&
    parsed.month === "all" &&
    parsed.dayOfWeek !== "all" &&
    parsed.minute !== "all" &&
    parsed.hour !== "all" &&
    (parsed.minute as Set<number>).size === 1 &&
    (parsed.hour as Set<number>).size === 1 &&
    (parsed.dayOfWeek as Set<number>).size === 1
  ) {
    const hh = [...(parsed.hour as Set<number>)][0];
    const mm = [...(parsed.minute as Set<number>)][0];
    const d = [...(parsed.dayOfWeek as Set<number>)][0] % 7;
    return `매주 ${DAY_NAMES[d]}요일 ${pad2(hh)}:${pad2(mm)}`;
  }
  return "표현식이 유효해요";
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * 표현식을 검증한다. 통과 시 ``description``을 함께 반환해 폼이 미리보기로
 * 노출할 수 있게 한다.
 */
export function validateCronExpression(expr: string): CronValidationResult {
  if (!expr.trim()) {
    return { valid: false, error: "표현식을 입력해 주세요." };
  }
  try {
    const parsed = parseCron(expr);
    return { valid: true, description: describe(expr, parsed) };
  } catch (err) {
    return {
      valid: false,
      error: err instanceof Error ? err.message : "표현식을 분석할 수 없어요.",
    };
  }
}

/**
 * ``parsed`` 표현식 기준으로 ``count``개의 다음 실행 시각을 계산한다.
 *
 * 알고리즘은 "다음 분부터 1분씩 증가하며 모든 필드와 매칭되는지 검사"한다.
 * 5필드 cron의 경우 1년치 분 = 525,600개로 항상 종료가 보장되며, 일반적인
 * 패턴(분/시 단위 매칭)에서는 수백 회 이내에 5건이 채워진다.
 *
 * 시간대는 환경의 로컬 타임존을 사용한다(브라우저 ``Date``의 기본 동작).
 * 백엔드는 APScheduler 기본인 시스템 로컬 타임존을 사용하므로 동일 정책이다.
 */
export function getNextRuns(
  expr: string,
  count: number,
  from: Date = new Date(),
): Date[] {
  const parsed = parseCron(expr);
  const out: Date[] = [];
  // 1분 뒤부터 시작 + 초·밀리초 절단 — cron은 분 단위 정밀도.
  const cursor = new Date(from);
  cursor.setSeconds(0, 0);
  cursor.setMinutes(cursor.getMinutes() + 1);

  // 안전 한계: 1년 분 수. 5필드 cron이라면 늦어도 이 안에 매칭이 발생.
  const maxIterations = 366 * 24 * 60;
  for (let i = 0; i < maxIterations && out.length < count; i++) {
    if (matches(cursor, parsed)) {
      out.push(new Date(cursor));
    }
    cursor.setMinutes(cursor.getMinutes() + 1);
  }
  return out;
}

function inSet(value: number, set: Set<number> | "all"): boolean {
  if (set === "all") return true;
  return set.has(value);
}

function matches(d: Date, parsed: ParsedCron): boolean {
  if (!inSet(d.getMinutes(), parsed.minute)) return false;
  if (!inSet(d.getHours(), parsed.hour)) return false;
  if (!inSet(d.getMonth() + 1, parsed.month)) return false;

  // crontab 규약: dom과 dow가 모두 명시되면 OR 결합. 한 쪽만 명시면 그것만.
  const dom = d.getDate();
  // ``getDay()``는 0=일, 7은 발생하지 않으므로 dow=7(일요일 별칭)도 매칭하도록
  // 두 값 모두 검사한다.
  const dow = d.getDay();
  const domStar = parsed.dayOfMonth === "all";
  const dowStar = parsed.dayOfWeek === "all";

  const domHit = inSet(dom, parsed.dayOfMonth);
  const dowHit =
    inSet(dow, parsed.dayOfWeek) ||
    (parsed.dayOfWeek !== "all" && parsed.dayOfWeek.has(7) && dow === 0);

  if (domStar && dowStar) return true;
  if (domStar) return dowHit;
  if (dowStar) return domHit;
  return domHit || dowHit;
}
