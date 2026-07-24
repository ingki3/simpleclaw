"""Issue 단위 verification evidence 의 구조화 ledger (BIZ-441).

"done means proven, not claimed" — PR CI, release CI, deploy/restart, health
smoke, product-intent 확인, subagent required gate 같은 검증 결과를 코멘트가
아닌 구조화된 단일 source 로 남긴다. done 판정은 이 evidence 데이터로만
계산한다:

- required stage 는 ``passed`` evidence 가 있어야 done 을 허용한다. evidence
  가 아예 없거나(missing) failed/pending/skipped 이면 ``done_allowed()`` 가
  False 다.
- required 목록에 없는 stage 의 evidence 는 상태와 무관하게 done 판정에
  관여하지 않는다 (optional evidence 는 참고 기록일 뿐이다).

설계 결정:

- **JSONL + 전체 rewrite.** record 수가 이슈 단위 stage 규모(십수 건)라
  subagent_ledger 와 같은 "load → mutate → tmp+replace 원자적 저장" 패턴을
  재사용한다. 부분 손상 라인은 경고 후 건너뛴다.
- **(issue_id, stage) 단위 upsert.** 같은 stage 를 재검증하면 기존 record 를
  갱신한다 — done 판정은 "그 stage 의 최신 상태" 하나만 보면 되고, 재시도
  이력은 raw_excerpt/summary 갱신으로 충분하다(created_at 은 최초 기록 보존).
- **redaction 은 저장 계층에서 강제.** raw_excerpt 는 어떤 경로로 들어와도
  시크릿 마스킹과 길이 제한을 통과한 뒤에만 디스크에 남는다 — 호출자
  실수가 곧 시크릿 유출이 되지 않게 한다.
- **parent 상태를 만지는 API 를 두지 않는다.** 이 ledger 는 done 허용 여부와
  근거만 반환한다 — issue 상태 전환은 상위 운영 흐름(Multica)의 몫이다.
- **시간 주입.** now 콜백을 받아 retention 을 테스트에서 결정적으로 재현한다.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]

# raw_excerpt 저장 상한 — evidence 는 "판정 근거 발췌"지 로그 아카이브가
# 아니다. 전체 로그는 CI/bot.log 가 원본이고 여기는 요약 가능한 꼬리만 남긴다.
MAX_RAW_EXCERPT_CHARS = 2000

# command/summary 저장 상한 — 한 줄 요약 필드가 로그 덤프로 오염되는 것을 막는다.
_MAX_FIELD_CHARS = 500

# 커스텀 stage slug 허용 형식 — 소문자 스네이크만. 자유 문자열을 그대로 받으면
# 오타("Unit", "pr-ci")가 별도 stage 로 갈라져 done 판정이 조용히 어긋난다.
_STAGE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# log_debug 와 같은 대표 시크릿 패턴 — evidence 발췌에는 CI 로그/명령 출력이
# 그대로 들어오므로 저장 전에 반드시 마스킹한다.
_SECRET_KEY_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|authorization)(\s*[=:]\s*)([^\s,;]+)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(\b\d{6,}:[A-Za-z0-9_-]{20,}\b)|"
    r"(gh[pousr]_[A-Za-z0-9_]+)|"
    r"(sk-[A-Za-z0-9._-]{8,})|"
    r"(AIza[0-9A-Za-z_-]{10,})|"
    r"([A-Za-z0-9_]{16,}:[A-Za-z0-9_\-]{20,})"
)


class VerificationStatus(str, Enum):
    """evidence 한 건의 판정 상태."""

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"


class VerificationStage(str, Enum):
    """운영 흐름에서 표준으로 쓰는 검증 stage 목록.

    ledger 자체는 slug 형식의 커스텀 stage 도 허용한다 — 이 enum 은 도구
    설명/운영 문서가 참조하는 canonical 이름 집합이다.
    """

    UNIT = "unit"
    LINT = "lint"
    PR_CI = "pr_ci"
    RELEASE_CI = "release_ci"
    DEPLOY = "deploy"
    RESTART = "restart"
    HEALTH_SMOKE = "health_smoke"
    PRODUCT_INTENT = "product_intent"
    SUBAGENT_GATE = "subagent_gate"


class VerificationLedgerError(Exception):
    """ledger 조작 중 발생한 검증/조회 오류."""


def _utcnow() -> datetime:
    """기본 now 제공자 — 테스트는 ledger 에 now 콜백을 주입해 고정한다."""
    return datetime.now(UTC)


def _parse_iso(value: object) -> datetime | None:
    """ISO8601 문자열을 timezone-aware datetime 으로 파싱한다.

    naive 값은 UTC 로 간주하고 ``Z`` 접미사도 허용한다. 파싱 실패는 None —
    깨진 timestamp 하나가 ledger 조회 전체를 죽이면 안 된다.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def normalize_stage(value: object) -> str:
    """stage 입력을 canonical slug 로 정규화한다.

    VerificationStage 값이면 그대로, 아니면 slug 형식 검사를 통과한 커스텀
    stage 만 허용한다. 대소문자/하이픈 오타로 stage 가 갈라지는 사고를 막는다.
    """
    if isinstance(value, VerificationStage):
        return value.value
    raw = str(value or "").strip()
    if not raw:
        raise VerificationLedgerError("stage 는 비어 있을 수 없습니다.")
    if not _STAGE_SLUG_RE.match(raw):
        known = ", ".join(s.value for s in VerificationStage)
        raise VerificationLedgerError(
            f"stage '{raw}' 형식이 올바르지 않습니다. 소문자 스네이크 slug 를 "
            f"사용하세요 (표준 stage: {known})."
        )
    return raw


def redact_excerpt(text: object, *, max_chars: int = MAX_RAW_EXCERPT_CHARS) -> str:
    """raw output 발췌에서 대표 시크릿을 마스킹하고 길이를 제한한다.

    저장 계층(ledger.record)이 항상 호출하므로 어떤 입력 경로로 들어와도
    디스크에는 redaction 을 통과한 값만 남는다. 길이 제한은 꼬리 우선 —
    실패 원인은 보통 출력 마지막에 있다.
    """
    value = str(text or "")
    # 값 패턴을 먼저 적용한다 — key 패턴이 "authorization: Bearer X" 의 "Bearer"
    # 만 소비하면 실제 토큰 X 가 남는다.
    value = _SECRET_VALUE_RE.sub(lambda m: f"{m.group(1) or ''}[REDACTED]", value)
    value = _SECRET_KEY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)
    if len(value) > max_chars:
        clipped = len(value) - max_chars
        value = f"[clipped {clipped} chars] ...{value[-max_chars:]}"
    return value


@dataclass
class VerificationEvidence:
    """검증 stage 한 건의 구조화 evidence."""

    id: str
    issue_id: str
    stage: str
    status: VerificationStatus
    pr_number: int | None = None
    commit_sha: str | None = None
    command: str = ""
    summary: str = ""
    raw_excerpt: str = ""
    source: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "issue_id": self.issue_id,
            "stage": self.stage,
            "status": self.status.value,
            "pr_number": self.pr_number,
            "commit_sha": self.commit_sha,
            "command": self.command,
            "summary": self.summary,
            "raw_excerpt": self.raw_excerpt,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VerificationEvidence:
        """저장분을 관대하게 복원한다.

        알 수 없는 status 는 ``pending`` 으로 정규화한다 — done 을 잘못
        허용하는 쪽(passed 간주)보다 잘못 막는 쪽이 안전하다(fail-closed).
        """
        try:
            status = VerificationStatus(str(raw.get("status") or ""))
        except ValueError:
            status = VerificationStatus.PENDING
        pr_number = raw.get("pr_number")
        try:
            pr_number = int(pr_number) if pr_number is not None else None
        except (TypeError, ValueError):
            pr_number = None
        return cls(
            id=str(raw.get("id") or ""),
            issue_id=str(raw.get("issue_id") or ""),
            stage=str(raw.get("stage") or ""),
            status=status,
            pr_number=pr_number,
            commit_sha=(str(raw["commit_sha"]) if raw.get("commit_sha") else None),
            command=str(raw.get("command") or ""),
            summary=str(raw.get("summary") or ""),
            raw_excerpt=str(raw.get("raw_excerpt") or ""),
            source=str(raw.get("source") or ""),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
        )


class VerificationEvidenceLedger:
    """JSONL verification evidence 저장소.

    subagent_ledger 와 같은 "load → mutate → 원자적 rewrite" 패턴을 쓴다.
    retention_days 가 지정되면 저장 시 terminal(passed/failed/skipped) 상태이면서
    보존 기간을 넘긴 record 만 정리한다 — pending record 는 검증 대기 증거이므로
    기간과 무관하게 보존한다.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int | None = None,
        now: NowFn | None = None,
    ) -> None:
        self._path = Path(path).expanduser()
        self._retention_days = retention_days
        self._now = now or _utcnow

    @property
    def path(self) -> Path:
        return self._path

    # -- 저장/로드 -----------------------------------------------------

    def load(self) -> list[VerificationEvidence]:
        if not self._path.is_file():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to read verification ledger %s: %s", self._path, exc
            )
            return []
        records: list[VerificationEvidence] = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(VerificationEvidence.from_dict(item))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed verification ledger line %d: %s",
                    line_no,
                    exc,
                )
        return records

    def _save_all(self, records: list[VerificationEvidence]) -> None:
        """tmp 파일에 전체를 쓰고 rename 으로 교체하는 원자적 저장."""
        records = self._apply_retention(records)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self._path)

    def _apply_retention(
        self, records: list[VerificationEvidence]
    ) -> list[VerificationEvidence]:
        """보존 기간을 넘긴 terminal record 를 정리한다.

        pending record 는 "아직 검증 안 됨" 을 증거로 남기는 상태이므로
        기간과 무관하게 보존한다.
        """
        if self._retention_days is None or self._retention_days <= 0:
            return records
        cutoff = self._now() - timedelta(days=self._retention_days)
        kept: list[VerificationEvidence] = []
        for record in records:
            if record.status is not VerificationStatus.PENDING:
                updated = _parse_iso(record.updated_at)
                if updated is not None and updated < cutoff:
                    continue
            kept.append(record)
        return kept

    # -- 기록 -----------------------------------------------------------

    def record(
        self,
        *,
        issue_id: str,
        stage: VerificationStage | str,
        status: VerificationStatus | str,
        pr_number: int | None = None,
        commit_sha: str | None = None,
        command: str = "",
        summary: str = "",
        raw_excerpt: str = "",
        source: str = "",
    ) -> VerificationEvidence:
        """evidence 를 기록한다 — 같은 (issue_id, stage) 는 upsert.

        upsert 시 created_at 은 최초 기록을 보존하고 나머지 필드는 최신
        검증 결과로 교체한다. raw_excerpt 는 저장 전 항상 redaction/길이
        제한을 거친다.
        """
        issue = str(issue_id or "").strip()
        if not issue:
            raise VerificationLedgerError("issue_id 는 비어 있을 수 없습니다.")
        stage_slug = normalize_stage(stage)
        try:
            if isinstance(status, VerificationStatus):
                status_value = status
            else:
                status_value = VerificationStatus(str(status).strip().lower())
        except ValueError:
            valid = ", ".join(s.value for s in VerificationStatus)
            raise VerificationLedgerError(
                f"status 는 {valid} 중 하나여야 합니다 (got '{status}')."
            ) from None

        now_iso = self._now().isoformat()
        records = self.load()
        existing = next(
            (r for r in records if r.issue_id == issue and r.stage == stage_slug),
            None,
        )
        record = VerificationEvidence(
            id=existing.id if existing else uuid.uuid4().hex[:12],
            issue_id=issue,
            stage=stage_slug,
            status=status_value,
            pr_number=pr_number,
            commit_sha=(str(commit_sha).strip() if commit_sha else None),
            # command/summary 에도 CLI 인자로 시크릿이 섞여 들어올 수 있다 —
            # 저장 계층에서 일괄 마스킹한다.
            command=redact_excerpt(
                str(command or "").strip(), max_chars=_MAX_FIELD_CHARS
            ),
            summary=redact_excerpt(
                str(summary or "").strip(), max_chars=_MAX_FIELD_CHARS
            ),
            raw_excerpt=redact_excerpt(raw_excerpt),
            source=str(source or "").strip(),
            created_at=existing.created_at if existing else now_iso,
            updated_at=now_iso,
        )
        if existing is not None:
            records[records.index(existing)] = record
        else:
            records.append(record)
        self._save_all(records)
        return record

    # -- 조회 ----------------------------------------------------------

    def list_by_issue(self, issue_id: str) -> list[VerificationEvidence]:
        """issue 단위 evidence 목록 (기록 순서 보존)."""
        target = str(issue_id).strip()
        return [r for r in self.load() if r.issue_id == target]

    def get(self, issue_id: str, stage: VerificationStage | str) -> VerificationEvidence | None:
        stage_slug = normalize_stage(stage)
        target = str(issue_id).strip()
        return next(
            (
                r
                for r in self.load()
                if r.issue_id == target and r.stage == stage_slug
            ),
            None,
        )

    # -- done 판정 -------------------------------------------------------

    @staticmethod
    def _normalize_required(
        required_stages: list[VerificationStage | str] | tuple | None,
    ) -> list[str]:
        """required stage 목록을 중복 제거된 slug 목록으로 정규화한다."""
        seen: list[str] = []
        for stage in required_stages or []:
            slug = normalize_stage(stage)
            if slug not in seen:
                seen.append(slug)
        return seen

    def missing_required_stages(
        self,
        issue_id: str,
        required_stages: list[VerificationStage | str],
    ) -> list[str]:
        """evidence 가 아예 없는 required stage 목록."""
        recorded = {r.stage for r in self.list_by_issue(issue_id)}
        return [
            stage
            for stage in self._normalize_required(required_stages)
            if stage not in recorded
        ]

    def done_report(
        self,
        issue_id: str,
        required_stages: list[VerificationStage | str],
    ) -> dict[str, Any]:
        """done 허용 여부와 차단 근거(stage 분류)를 함께 반환한다.

        required 목록에 없는 stage 의 evidence 는 어떤 상태여도 판정에
        관여하지 않는다 — optional 참고 기록은 done 을 막지 않는다.
        """
        required = self._normalize_required(required_stages)
        by_stage = {r.stage: r for r in self.list_by_issue(issue_id)}
        missing: list[str] = []
        failed: list[str] = []
        incomplete: list[str] = []
        for stage in required:
            evidence = by_stage.get(stage)
            if evidence is None:
                missing.append(stage)
            elif evidence.status is VerificationStatus.FAILED:
                failed.append(stage)
            elif evidence.status is not VerificationStatus.PASSED:
                # pending/skipped — 아직 증명되지 않은 required stage.
                incomplete.append(stage)
        return {
            "done_allowed": not (missing or failed or incomplete),
            "required_stages": required,
            "missing_stages": missing,
            "failed_stages": failed,
            "incomplete_stages": incomplete,
        }

    def done_allowed(
        self,
        issue_id: str,
        required_stages: list[VerificationStage | str],
    ) -> bool:
        """required stage 가 전부 passed evidence 를 가질 때만 True."""
        return bool(self.done_report(issue_id, required_stages)["done_allowed"])


__all__ = [
    "MAX_RAW_EXCERPT_CHARS",
    "VerificationEvidence",
    "VerificationEvidenceLedger",
    "VerificationLedgerError",
    "VerificationStage",
    "VerificationStatus",
    "normalize_stage",
    "redact_excerpt",
]
