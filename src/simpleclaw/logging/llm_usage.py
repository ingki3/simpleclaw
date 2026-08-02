"""Durable, content-free LLM usage accounting and threshold claims."""

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
)

logger = logging.getLogger(__name__)
_GROUP_COLUMNS = {"backend": "backend_name", "model": "model", "route": "route_name", "task": "task_name"}


@dataclass(frozen=True)
class UsageThresholdClaim:
    period_kind: str
    period_key: str
    threshold_microusd: int
    claimed_at_utc: str
    observed_cost_microusd: int


class LLMUsageStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        run_usage_migrations(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def record(self, event: LLMUsageEvent) -> bool:
        values = (
            event.event_id, event.occurred_at_utc, event.trace_id, event.backend_name,
            event.provider_profile, event.model, event.route_name, event.task_name,
            event.attempt_role, event.retry_reason, event.status, event.duration_ms,
            event.usage.input_tokens, event.usage.output_tokens,
            event.usage.cache_read_input_tokens, event.usage.cache_write_input_tokens,
            event.usage.reasoning_tokens, int(event.usage.usage_known),
            event.usage.provider_reported_cost_microusd,
            event.estimated_cost_microusd, event.pricing_version, event.error_type,
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

    def summarize_range(self, *, start_utc: datetime, end_utc: datetime, group_by: str | None = None) -> dict:
        column = None
        if group_by is not None:
            column = _GROUP_COLUMNS.get(group_by)
            if column is None:
                raise ValueError("invalid group_by")
        where = "occurred_at_utc >= ? AND occurred_at_utc < ?"
        params = (start_utc.astimezone(UTC).isoformat(), end_utc.astimezone(UTC).isoformat())
        aggregates = "COUNT(*) event_count, COALESCE(SUM(input_tokens),0) input_tokens, COALESCE(SUM(output_tokens),0) output_tokens, COALESCE(SUM(estimated_cost_microusd),0) estimated_cost_microusd, SUM(CASE WHEN estimated_cost_microusd IS NOT NULL THEN 1 ELSE 0 END) priced_event_count, SUM(CASE WHEN estimated_cost_microusd IS NULL THEN 1 ELSE 0 END) unpriced_event_count, COALESCE(SUM(CASE WHEN estimated_cost_microusd IS NULL THEN COALESCE(input_tokens,0)+COALESCE(output_tokens,0) ELSE 0 END),0) unpriced_tokens"
        conn = self._connect()
        try:
            row = conn.execute(f"SELECT {aggregates} FROM llm_usage_events WHERE {where}", params).fetchone()
            result = dict(row)
            result["estimated_cost_usd"] = str(microusd_to_decimal_usd(result["estimated_cost_microusd"]))
            result["groups"] = []
            if column:
                rows = conn.execute(f"SELECT {column} group_value, {aggregates} FROM llm_usage_events WHERE {where} GROUP BY {column} ORDER BY estimated_cost_microusd DESC LIMIT 100", params).fetchall()
                result["groups"] = [dict(item) for item in rows]
                for item in result["groups"]:
                    item[column] = item.pop("group_value")
                    item["estimated_cost_usd"] = str(microusd_to_decimal_usd(item["estimated_cost_microusd"]))
            return result
        finally:
            conn.close()

    @staticmethod
    def _window(now_utc: datetime, timezone: str, kind: str) -> tuple[datetime, datetime, str]:
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

    def summarize_day(self, now_utc: datetime, *, timezone: str, group_by: str | None = None) -> dict:
        start, end, _ = self._window(now_utc, timezone, "day")
        return self.summarize_range(start_utc=start, end_utc=end, group_by=group_by)

    def summarize_month(self, now_utc: datetime, *, timezone: str, group_by: str | None = None) -> dict:
        start, end, _ = self._window(now_utc, timezone, "month")
        return self.summarize_range(start_utc=start, end_utc=end, group_by=group_by)

    def claim_threshold(self, claim: UsageThresholdClaim) -> bool:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute("INSERT OR IGNORE INTO llm_usage_alert_claims(period_kind,period_key,threshold_microusd,claimed_at_utc,observed_cost_microusd) VALUES (?,?,?,?,?)", (claim.period_kind, claim.period_key, claim.threshold_microusd, claim.claimed_at_utc, claim.observed_cost_microusd))
            return cur.rowcount == 1
        finally:
            conn.close()

    def _mark(self, claim: UsageThresholdClaim, status: str, error_type: str | None = None) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("UPDATE llm_usage_alert_claims SET dispatch_status=?, error_type=? WHERE period_kind=? AND period_key=? AND threshold_microusd=?", (status, error_type, claim.period_kind, claim.period_key, claim.threshold_microusd))
        finally:
            conn.close()

    def mark_alert_sent(self, claim: UsageThresholdClaim) -> None:
        self._mark(claim, "sent")

    def mark_alert_failed(self, claim: UsageThresholdClaim, error_type: str) -> None:
        self._mark(claim, "failed", error_type)

    def prune_before(self, cutoff_utc: str) -> int:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute("DELETE FROM llm_usage_events WHERE occurred_at_utc < ?", (cutoff_utc,))
            return cur.rowcount
        finally:
            conn.close()


class LLMUsageService:
    def __init__(self, store: LLMUsageStore, *, pricing: dict[str, BackendPricing] | None = None, metrics: object | None = None, structured_logger: object | None = None, timezone: str = "Asia/Seoul", daily_usd: Decimal | None = None, monthly_usd: Decimal | None = None, alert_callback=None) -> None:
        self.store = store
        self.pricing = pricing or {}
        self.metrics = metrics
        self.structured_logger = structured_logger
        self.timezone = timezone
        self.thresholds = {"day": daily_usd, "month": monthly_usd}
        self.alert_callback = alert_callback

    def record(self, event: LLMUsageEvent) -> None:
        try:
            price = self.pricing.get(event.backend_name)
            cost = estimate_cost_microusd(event.usage, price)
            persisted = event.with_cost(cost, price.version if price else None)
            if not self.store.record(persisted):
                return
            if self.metrics and hasattr(self.metrics, "record_llm_usage"):
                self.metrics.record_llm_usage(persisted)
            self._log(persisted)
            self._check_thresholds(persisted)
        except Exception as exc:  # accounting must never fail the LLM response
            if self.metrics and hasattr(self.metrics, "record_llm_usage_failure"):
                self.metrics.record_llm_usage_failure()
            logger.warning("llm_usage_record_failed error_type=%s", type(exc).__name__)

    def _log(self, event: LLMUsageEvent) -> None:
        if not self.structured_logger or not hasattr(self.structured_logger, "log"):
            return
        self.structured_logger.log(action_type="llm_usage", status=event.status, duration_ms=event.duration_ms, input_summary="", output_summary="", trace_id=event.trace_id, event_id=event.event_id, backend_name=event.backend_name, provider_profile=event.provider_profile, model=event.model, route_name=event.route_name, task_name=event.task_name, attempt_role=event.attempt_role, retry_reason=event.retry_reason, usage_known=event.usage.usage_known, input_tokens=event.usage.input_tokens, output_tokens=event.usage.output_tokens, estimated_cost_microusd=event.estimated_cost_microusd, pricing_version=event.pricing_version)

    def _check_thresholds(self, event: LLMUsageEvent) -> None:
        if not self.alert_callback or event.estimated_cost_microusd is None:
            return
        now = datetime.fromisoformat(event.occurred_at_utc)
        for kind, threshold in self.thresholds.items():
            if threshold is None:
                continue
            summary = self.store.summarize_day(now, timezone=self.timezone) if kind == "day" else self.store.summarize_month(now, timezone=self.timezone)
            threshold_micro = int(threshold * Decimal(1_000_000))
            if summary["estimated_cost_microusd"] < threshold_micro:
                continue
            _, _, key = self.store._window(now, self.timezone, kind)
            claim = UsageThresholdClaim(kind, key, threshold_micro, datetime.now(UTC).isoformat(), summary["estimated_cost_microusd"])
            if self.store.claim_threshold(claim):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    continue
                loop.create_task(self._dispatch(claim, summary))

    async def _dispatch(self, claim: UsageThresholdClaim, summary: dict) -> None:
        try:
            result = self.alert_callback(format_usage_alert(claim, summary, self.timezone))
            if asyncio.iscoroutine(result):
                await result
            self.store.mark_alert_sent(claim)
        except Exception as exc:
            self.store.mark_alert_failed(claim, type(exc).__name__)


def format_usage_alert(claim: UsageThresholdClaim, summary: dict, timezone: str) -> str:
    label = "일일" if claim.period_kind == "day" else "월간"
    return (f"⚠️ LLM {label} 비용 임계 도달\n- period: {claim.period_key} ({timezone})\n- estimated cost: ${microusd_to_decimal_usd(claim.observed_cost_microusd)} / threshold ${microusd_to_decimal_usd(claim.threshold_microusd)}\n- tokens: input {summary['input_tokens']:,} · output {summary['output_tokens']:,}\n- unpriced events: {summary['unpriced_event_count']}\n- action: alert-only; 호출은 자동 차단하지 않았습니다.")
