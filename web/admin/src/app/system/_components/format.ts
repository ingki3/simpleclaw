/**
 * System 화면 전용 포매터 — uptime / 바이트 / 비율 표현을 한 곳에 모은다.
 *
 * 다른 화면에서 같은 표현이 필요하면 `lib/format.ts`로 승격해 공유한다.
 */

const KB = 1024;
const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  if (bytes <= 0) return "0 B";
  const idx = Math.min(
    Math.floor(Math.log(bytes) / Math.log(KB)),
    UNITS.length - 1,
  );
  const v = bytes / Math.pow(KB, idx);
  // GB 이상은 소수 1자리, 그 이하는 정수로 표기 — 의미 노이즈 최소화.
  const formatted = idx >= 3 ? v.toFixed(1) : Math.round(v).toString();
  return `${formatted} ${UNITS[idx]}`;
}

export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return h > 0 ? `${d}d ${h}h` : `${d}d`;
}

export function percent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}
