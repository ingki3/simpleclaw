"""LLM 사용량을 내용 없이 영속 집계하고 임계 경보 claim을 관리한다.

이 모듈은 저장 직전 문자열을 다시 정규화하는 방어 경계를 제공하고, SQLite를
일·월 비용 집계의 SoT로 사용한다. 경보 claim은 lease와 제한된 재시도로 장애 후
재획득할 수 있으며, 회계·경보 오류는 정상 LLM 응답을 막지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from simpleclaw.db.migrations import run_usage_migrations
from simpleclaw.llm.usage import (
    BackendPricing,
    LLMUsageEvent,
    estimate_cost_microusd,
    microusd_to_decimal_usd,
    sanitize_usage_dimension,
)

logger = logging.getLogger(__name__)
_GROUP_COLUMNS = {
    "backend": "backend_name",
    "model": "model",
    "route": "route_name",
    "task": "task_name",
}


@dataclass(frozen=True)
class UsageThresholdClaim:
    """기간·임계값별로 중복 제거되는 경보 발송 claim이다."""

    period_kind: str
    period_key: str
    threshold_microusd: int
    claimed_at_utc: str
    observed_cost_microusd: int


class LLMUsageStore:
    """별도 SQLite DB에 usage event와 durable alert claim을 저장한다."""

    def __init__(self, db_path: str | Path) -> None:
        """DB 경로를 고정하고 usage 전용 migration을 적용한다."""
        self.db_path = Path(db_path)
        run_usage_migrations(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        """짧은 transaction마다 WAL·foreign key 설정이 적용된 연결을 연다."""
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def record(self, event: LLMUsageEvent) -> bool:
        """문자열을 fail-closed 정규화한 event를 idempotent하게 저장한다."""
        event = event.sanitized()
        values = (
            event.event_id,
            event.occurred_at_utc,
            event.trace_id,
            event.backend_name,
            event.provider_profile,
            event.model,
            event.route_name,
            event.task_name,
            event.attempt_role,
            event.retry_reason,
            event.status,
            event.duration_ms,
            event.usage.input_tokens,
            event.usage.output_tokens,
            event.usage.cache_read_input_tokens,
            event.usage.cache_write_input_tokens,
            event.usage.reasoning_tokens,
            int(event.usage.usage_known),
            event.usage.provider_reported_cost_microusd,
            event.estimated_cost_microusd,
            event.pricing_version,
            event.error_type,
        )
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO llm_usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
            return cur.rowcount == 1
        finally:
            conn.close()

    def summarize_range(
        self, *, start_utc: datetime, end_utc: datetime, group_by: str | None = None
    ) -> dict:
        """허용된 dimension으로만 bounded 기간 집계를 반환한다."""
        column = None
        if group_by is not None:
            column = _GROUP_COLUMNS.get(group_by)
            if column is None:
                raise ValueError("invalid group_by")
        where = "occurred_at_utc >= ? AND occurred_at_utc < ?"
        params = (
            start_utc.astimezone(UTC).isoformat(),
            end_utc.astimezone(UTC).isoformat(),
        )
        aggregates = "COUNT(*) event_count, COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens, COALESCE(SUM(estimated_cost_microusd),0) estimated_cost_microusd, SUM(CASE WHEN estimated_cost_microusd IS NOT NULL THEN 1 ELSE 0 END) priced_event_count, SUM(CASE WHEN estimated_cost_microusd IS NULL THEN 1 ELSE 0 END) unpriced_event_count, COALESCE(SUM(CASE WHEN estimated_cost_microusd IS NULL THEN COALESCE(input_tokens,0)+COALESCE(output_tokens,0) ELSE 0 END),0) unpriced_tokens"
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT {aggregates} FROM llm_usage_events WHERE {where}", params
            ).fetchone()
            result = dict(row)
            result["estimated_cost_usd"] = str(
                microusd_to_decimal_usd(result["estimated_cost_microusd"])
            )
            result["groups"] = []
            if column:
                rows = conn.execute(
                    f"SELECT {column} group_value, {aggregates} FROM llm_usage_events WHERE {where} GROUP BY {column} ORDER BY estimated_cost_microusd DESC LIMIT 100",
                    params,
                ).fetchall()
                result["groups"] = [dict(item) for item in rows]
                for item in result["groups"]:
                    item[column] = item.pop("group_value")
                    item["estimated_cost_usd"] = str(
                        microusd_to_decimal_usd(item["estimated_cost_microusd"])
                    )
            return result
        finally:
            conn.close()

    @staticmethod
    def _window(
        now_utc: datetime, timezone: str, kind: str
    ) -> tuple[datetime, datetime, str]:
        """설정 timezone의 일·월 경계를 UTC와 durable key로 변환한다."""
        local = now_utc.astimezone(ZoneInfo(timezone))
        if kind == "day":
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            key = start.date().isoformat()
        else:
            start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            key = start.strftime("%Y-%m")
        return start.astimezone(UTC), end.astimezone(UTC), key

    def summarize_day(
        self, now_utc: datetime, *, timezone: str, group_by: str | None = None
    ) -> dict:
        """현재 local day의 usage 집계를 반환한다."""
        start, end, _ = self._window(now_utc, timezone, "day")
        return self.summarize_range(start_utc=start, end_utc=end, group_by=group_by)

    def summarize_month(
        self, now_utc: datetime, *, timezone: str, group_by: str | None = None
    ) -> dict:
        """현재 local month의 usage 집계를 반환한다."""
        start, end, _ = self._window(now_utc, timezone, "month")
        return self.summarize_range(start_utc=start, end_utc=end, group_by=group_by)

    def claim_threshold(
        self,
        claim: UsageThresholdClaim,
        *,
        now_utc: datetime | None = None,
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> bool:
        """새 claim 또는 retry 가능한 failed/expired claim을 원자적으로 획득한다."""
        if claim.period_kind not in {"day", "month"}:
            return False
        expected_key_length = 10 if claim.period_kind == "day" else 7
        if len(claim.period_key) != expected_key_length or not all(
            char.isdigit() or char == "-" for char in claim.period_key
        ):
            return False
        try:
            claimed_at = datetime.fromisoformat(claim.claimed_at_utc)
        except (TypeError, ValueError):
            return False
        if claimed_at.tzinfo is None:
            return False
        claimed_at_text = claim.claimed_at_utc
        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        now_text = now.isoformat()
        lease_text = (
            now + timedelta(seconds=max(1, min(lease_seconds, 3_600)))
        ).isoformat()
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO llm_usage_alert_claims("
                    "period_kind,period_key,threshold_microusd,claimed_at_utc,"
                    "observed_cost_microusd,dispatch_status,attempt_count,"
                    "lease_expires_at_utc) VALUES (?,?,?,?,?,'pending',1,?)",
                    (
                        claim.period_kind,
                        claim.period_key,
                        claim.threshold_microusd,
                        claimed_at_text,
                        claim.observed_cost_microusd,
                        lease_text,
                    ),
                )
                if cur.rowcount == 1:
                    return True
                cur = conn.execute(
                    "UPDATE llm_usage_alert_claims SET claimed_at_utc=?, "
                    "observed_cost_microusd=?, dispatch_status='pending', "
                    "attempt_count=attempt_count+1, lease_expires_at_utc=?, "
                    "next_attempt_at_utc=NULL, error_type=NULL "
                    "WHERE period_kind=? AND period_key=? AND threshold_microusd=? "
                    "AND attempt_count < ? AND ("
                    "(dispatch_status='failed' AND (next_attempt_at_utc IS NULL OR next_attempt_at_utc<=?)) "
                    "OR (dispatch_status='pending' AND (lease_expires_at_utc IS NULL OR lease_expires_at_utc<=?)))",
                    (
                        claimed_at_text,
                        claim.observed_cost_microusd,
                        lease_text,
                        claim.period_kind,
                        claim.period_key,
                        claim.threshold_microusd,
                        max(1, min(max_attempts, 10)),
                        now_text,
                        now_text,
                    ),
                )
                return cur.rowcount == 1
        finally:
            conn.close()

    def _mark(
        self, claim: UsageThresholdClaim, status: str, error_type: str | None = None
    ) -> None:
        """claim의 terminal dispatch 상태를 짧은 transaction으로 갱신한다."""
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE llm_usage_alert_claims SET dispatch_status=?, error_type=?, "
                    "lease_expires_at_utc=NULL, next_attempt_at_utc=NULL "
                    "WHERE period_kind=? AND period_key=? AND threshold_microusd=? "
                    "AND claimed_at_utc=? AND dispatch_status='pending'",
                    (
                        status,
                        error_type,
                        claim.period_kind,
                        claim.period_key,
                        claim.threshold_microusd,
                        claim.claimed_at_utc,
                    ),
                )
        finally:
            conn.close()

    def mark_alert_sent(self, claim: UsageThresholdClaim) -> None:
        """성공한 claim을 재획득 불가능한 sent 상태로 확정한다."""
        self._mark(claim, "sent")

    def mark_alert_failed(
        self,
        claim: UsageThresholdClaim,
        error_type: str,
        *,
        retry_cooldown_seconds: int,
        now_utc: datetime | None = None,
    ) -> None:
        """실패 claim에 다음 재시도 가능 시각을 durable하게 기록한다."""
        now = (now_utc or datetime.now(UTC)).astimezone(UTC)
        next_attempt = (
            now + timedelta(seconds=max(0, min(retry_cooldown_seconds, 86_400)))
        ).isoformat()
        safe_error_type = sanitize_usage_dimension(error_type, field="error_type")
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE llm_usage_alert_claims SET dispatch_status='failed', "
                    "error_type=?, lease_expires_at_utc=NULL, next_attempt_at_utc=? "
                    "WHERE period_kind=? AND period_key=? AND threshold_microusd=? "
                    "AND claimed_at_utc=? AND dispatch_status='pending'",
                    (
                        safe_error_type,
                        next_attempt,
                        claim.period_kind,
                        claim.period_key,
                        claim.threshold_microusd,
                        claim.claimed_at_utc,
                    ),
                )
        finally:
            conn.close()

    def get_alert_claim(self, claim: UsageThresholdClaim) -> dict | None:
        """테스트·운영 진단용으로 특정 claim의 content-free 상태를 반환한다."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM llm_usage_alert_claims WHERE period_kind=? "
                "AND period_key=? AND threshold_microusd=?",
                (claim.period_kind, claim.period_key, claim.threshold_microusd),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def prune_before(self, cutoff_utc: str) -> int:
        """retention 경계보다 오래된 event를 삭제하고 건수를 반환한다."""
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "DELETE FROM llm_usage_events WHERE occurred_at_utc < ?",
                    (cutoff_utc,),
                )
            return cur.rowcount
        finally:
            conn.close()


class LLMUsageService:
    """가격 계산·저장·metric·log·threshold alert를 fail-open으로 조합한다."""

    def __init__(
        self,
        store: LLMUsageStore,
        *,
        pricing: dict[str, BackendPricing] | None = None,
        metrics: object | None = None,
        structured_logger: object | None = None,
        timezone: str = "Asia/Seoul",
        daily_usd: Decimal | None = None,
        monthly_usd: Decimal | None = None,
        alert_callback=None,
        alert_cooldown_seconds: int = 3600,
        alert_max_attempts: int = 3,
        alert_lease_seconds: int = 300,
    ) -> None:
        """의존성과 bounded alert retry 정책을 고정한다."""
        self.store = store
        self.pricing = pricing or {}
        self.metrics = metrics
        self.structured_logger = structured_logger
        self.timezone = timezone
        self.thresholds = {"day": daily_usd, "month": monthly_usd}
        self.alert_callback = alert_callback
        self.alert_cooldown_seconds = max(0, min(alert_cooldown_seconds, 86_400))
        self.alert_max_attempts = max(1, min(alert_max_attempts, 10))
        self.alert_lease_seconds = max(1, min(alert_lease_seconds, 3_600))

    def record(self, event: LLMUsageEvent) -> None:
        """event를 정규화·가격 계산 후 기록하되 모든 회계 장애를 격리한다."""
        try:
            event = event.sanitized()
            price = self.pricing.get(event.backend_name)
            cost = estimate_cost_microusd(event.usage, price)
            persisted = event.with_cost(cost, price.version if price else None)
            if not self.store.record(persisted):
                return
            if self.metrics and hasattr(self.metrics, "record_llm_usage"):
                self.metrics.record_llm_usage(persisted)
            self._log(persisted)
            self._check_thresholds(persisted)
        except Exception as exc:  # noqa: BLE001 — accounting is deliberately fail-open.
            if self.metrics and hasattr(self.metrics, "record_llm_usage_failure"):
                self.metrics.record_llm_usage_failure()
            logger.warning("llm_usage_record_failed error_type=%s", type(exc).__name__)

    def _log(self, event: LLMUsageEvent) -> None:
        """내용 필드가 비어 있는 bounded structured usage log만 남긴다."""
        if not self.structured_logger or not hasattr(self.structured_logger, "log"):
            return
        self.structured_logger.log(
            action_type="llm_usage",
            status=event.status,
            duration_ms=event.duration_ms,
            input_summary="",
            output_summary="",
            trace_id=event.trace_id,
            event_id=event.event_id,
            backend_name=event.backend_name,
            provider_profile=event.provider_profile,
            model=event.model,
            route_name=event.route_name,
            task_name=event.task_name,
            attempt_role=event.attempt_role,
            retry_reason=event.retry_reason,
            usage_known=event.usage.usage_known,
            input_tokens=event.usage.input_tokens,
            output_tokens=event.usage.output_tokens,
            estimated_cost_microusd=event.estimated_cost_microusd,
            pricing_version=event.pricing_version,
        )

    def _check_thresholds(self, event: LLMUsageEvent) -> None:
        """도달한 일·월 임계값의 새 claim 또는 due retry를 비동기 발송한다."""
        if not self.alert_callback or event.estimated_cost_microusd is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        now = datetime.fromisoformat(event.occurred_at_utc)
        for kind, threshold in self.thresholds.items():
            if threshold is None:
                continue
            summary = (
                self.store.summarize_day(now, timezone=self.timezone)
                if kind == "day"
                else self.store.summarize_month(now, timezone=self.timezone)
            )
            threshold_micro = int(threshold * Decimal(1_000_000))
            if summary["estimated_cost_microusd"] < threshold_micro:
                continue
            _, _, key = self.store._window(now, self.timezone, kind)
            claimed_at = datetime.now(UTC)
            claim = UsageThresholdClaim(
                kind,
                key,
                threshold_micro,
                claimed_at.isoformat(),
                summary["estimated_cost_microusd"],
            )
            if self.store.claim_threshold(
                claim,
                now_utc=claimed_at,
                lease_seconds=self.alert_lease_seconds,
                max_attempts=self.alert_max_attempts,
            ):
                loop.create_task(self._dispatch(claim, summary))

    async def _dispatch(self, claim: UsageThresholdClaim, summary: dict) -> None:
        """operator callback을 await하고 claim을 sent 또는 retryable failed로 남긴다."""
        try:
            result = self.alert_callback(
                format_usage_alert(claim, summary, self.timezone)
            )
            if asyncio.iscoroutine(result):
                await result
            self.store.mark_alert_sent(claim)
        except Exception as exc:  # noqa: BLE001 — alert delivery is deliberately fail-open.
            self.store.mark_alert_failed(
                claim,
                type(exc).__name__,
                retry_cooldown_seconds=self.alert_cooldown_seconds,
            )


def format_usage_alert(claim: UsageThresholdClaim, summary: dict, timezone: str) -> str:
    """개별 backend나 사용자 식별자를 포함하지 않는 operator 경보를 만든다."""
    label = "일일" if claim.period_kind == "day" else "월간"
    return f"⚠️ LLM {label} 비용 임계 도달\n- period: {claim.period_key} ({timezone})\n- estimated cost: ${microusd_to_decimal_usd(claim.observed_cost_microusd)} / threshold ${microusd_to_decimal_usd(claim.threshold_microusd)}\n- tokens: input {summary['input_tokens']:,} · output {summary['output_tokens']:,}\n- unpriced events: {summary['unpriced_event_count']}\n- action: alert-only; 호출은 자동 차단하지 않았습니다."
