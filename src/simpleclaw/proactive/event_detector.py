"""외부/런타임 이벤트를 proactive opportunity로 변환하는 adapter.

이 모듈은 이벤트를 직접 사용자에게 알리지 않는다. cron failure 같은 즉시성 이벤트를
OpportunityStore에 pending 후보로만 적재해 presenter/policy 단계가 별도로 판단하게 한다.
"""

from __future__ import annotations

import json
import re
from typing import Any

from simpleclaw.proactive.models import (
    OpportunityType,
    ProactiveOpportunity,
    SuggestedAction,
    SuggestedActionKind,
)
from simpleclaw.proactive.store import OpportunityStore

_SECRET_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[a-z0-9._\-]+")


class EventDetector:
    """cron/Multica/GitHub 같은 이벤트 payload를 pending 후보로 변환한다."""

    def __init__(
        self,
        *,
        store: OpportunityStore | None = None,
        enabled: bool = False,
        cron_failure_enabled: bool = False,
    ) -> None:
        """상위 feature flag와 이벤트 종류별 flag를 fail-closed로 보관한다."""
        self._store = store
        self.enabled = bool(enabled)
        self.cron_failure_enabled = bool(cron_failure_enabled)

    def capture_cron_event(
        self,
        *,
        event_type: str,
        job_name: str,
        error_details: str | None = None,
        result_summary: str | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ProactiveOpportunity | None:
        """cron 이벤트 중 failure/circuit-break만 복구 후보로 저장한다."""
        normalized_event = str(event_type or "").strip().lower()
        if not self.enabled or not self.cron_failure_enabled:
            return None
        if normalized_event not in {"failure", "retry_exhausted", "circuit_break"}:
            return None
        safe_error = self._redact(error_details or "")
        safe_payload = self._redact_payload(payload or {})
        evidence = [
            f"event_type={normalized_event}",
            f"job_name={self._redact(job_name)}",
        ]
        if attempt is not None:
            evidence.append(f"attempt={attempt}")
        if max_attempts is not None:
            evidence.append(f"max_attempts={max_attempts}")
        if safe_error:
            evidence.append(f"error_details={safe_error[:300]}")
        if safe_payload:
            evidence.append(f"payload={safe_payload[:300]}")
        if result_summary:
            evidence.append(f"result_summary={self._redact(result_summary)[:200]}")

        priority = 4 if normalized_event == "circuit_break" else 3
        urgency = 3 if normalized_event == "circuit_break" else 2
        opportunity = ProactiveOpportunity(
            type=OpportunityType.FAILURE_RECOVERY,
            title=f"Cron 실패 복구 후보: {self._redact(job_name)}",
            message_draft=(
                f"Cron job '{self._redact(job_name)}' 실행이 실패했습니다. "
                "원인 확인 또는 재시도 방안을 검토할까요?"
            ),
            evidence=evidence,
            confidence=0.86,
            priority=priority,
            urgency=urgency,
            cooldown_key=f"event:cron_failure:{job_name}",
            suggested_action=SuggestedAction(
                kind=SuggestedActionKind.OPEN_REVIEW,
                label="cron 실패 복구 검토",
                payload={"source": "event_hook", "event_type": normalized_event, "job_name": job_name},
            ),
            requires_user_approval=True,
            source="event_hook:cron",
        )
        if self._store is None:
            return opportunity
        return self._store.upsert_pending_by_cooldown_key(opportunity)

    def _redact(self, value: object) -> str:
        """문자열 안의 key=value 스타일 secret을 제거한다."""
        text = str(value or "")
        text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
        return _BEARER_RE.sub("Bearer [REDACTED]", text)

    def _redact_payload(self, payload: dict[str, Any]) -> str:
        """중첩 payload를 순회하며 secret key의 값을 마스킹한 JSON 문자열로 만든다."""
        def scrub(value: Any) -> Any:
            if isinstance(value, dict):
                out: dict[str, Any] = {}
                for key, item in value.items():
                    if _SECRET_KEY_RE.search(str(key)):
                        out[str(key)] = "[REDACTED]"
                    else:
                        out[str(key)] = scrub(item)
                return out
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, str):
                return self._redact(value)
            return value

        return json.dumps(scrub(payload), ensure_ascii=False, sort_keys=True)
