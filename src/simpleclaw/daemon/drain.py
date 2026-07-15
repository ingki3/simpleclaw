"""LaunchAgent restart 를 위한 drain/quiesce 상태 관리 (BIZ-442).

runtime-affecting deploy 에서 ``launchctl kickstart -k`` 로 봇을 재시작하기 전에
새 intake(텔레그램 메시지, 웹훅, cron 실행)를 잠시 거절하고 이미 실행 중인
turn 은 완료되도록 두는 "drain 창"을 제공한다.

설계 결정:

- **drain 요청은 파일 기반.** deploy script(별도 프로세스)가 drain 을 요청하고
  bot 프로세스가 그 상태를 관찰해야 하므로, 공유 상태는 JSON state 파일 하나로
  둔다(tmp+replace 원자적 쓰기 — verification_ledger 와 같은 패턴). SQLite 는
  이 규모(필드 5개, 쓰기 빈도 배포 시 2회)에 과하다.
- **deadline 지나면 자동 해제.** deploy script 가 clear 없이 죽어도 봇이 영구히
  intake 를 거절하는 사고가 없도록, ``is_draining()`` 은 deadline 경과 시
  False 를 반환한다. drain 은 가용성 편의 장치이지 보안 게이트가 아니다.
- **state 파일이 깨져도 fail-open.** 손상된 drain 파일 하나가 서비스 전체를
  막으면 안 되므로, 파싱 실패는 경고 로그 후 "draining 아님"으로 취급한다.
- **active operation 카운터는 in-memory.** 실행 중 turn 수는 봇 프로세스만 알 수
  있는 정보다. 외부(deploy script)는 admin health 엔드포인트를 폴링해 이 값을
  본다 — 프로세스 간 공유 카운터를 파일로 흉내 내지 않는다(crash 시 stale
  카운터가 drain 을 영원히 못 끝내게 만든다).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

NowFn = Callable[[], datetime]

# drain 요청의 기본 유효 시간(초). deploy script 의 "active operation 이 빠질
# 때까지 기다리는 창"과 같은 값을 쓴다 — 이 시간 안에 restart 가 일어나지
# 않으면 drain 은 자동 만료되어 서비스가 정상 intake 로 복귀한다.
DEFAULT_DRAIN_TIMEOUT_SECONDS = 120.0

# drain 중 새 intake 에 보내는 사용자-facing 안내. 채널(텔레그램)과
# 오케스트레이터가 같은 문구를 쓰도록 한곳에서 관리한다.
DRAIN_MAINTENANCE_MESSAGE = (
    "🔧 점검(재시작) 중입니다. 잠시 후 다시 보내주세요."
)

# drain 중 cron 실행을 건너뛸 때의 결과 메시지 — notifier 를 통해 운영자에게
# 전달될 수 있으므로 "왜 실행이 안 됐는지"를 명시한다.
DRAIN_CRON_SKIPPED_MESSAGE = (
    "⏸️ 점검(재시작) drain 중이라 이번 cron 실행을 건너뛰었습니다."
)


def _utcnow() -> datetime:
    """기본 now 제공자 — 테스트는 now 콜백을 주입해 시간을 고정한다."""
    return datetime.now(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    """ISO8601 문자열을 timezone-aware datetime 으로 관대하게 파싱한다."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class DrainState:
    """drain 요청 한 건의 스냅샷."""

    draining: bool
    reason: str = ""
    requested_at: str | None = None
    deadline: str | None = None
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "draining": self.draining,
            "reason": self.reason,
            "requested_at": self.requested_at,
            "deadline": self.deadline,
            "source": self.source,
        }

    @classmethod
    def idle(cls) -> "DrainState":
        """drain 요청이 없는 기본 상태."""
        return cls(draining=False)


class DrainController:
    """drain 상태 파일 + in-process active operation 카운터.

    bot 프로세스와 deploy script 가 같은 ``state_file`` 경로를 바라보는 각자의
    인스턴스를 만들어 쓴다. ``request_drain``/``clear_drain`` 은 어느 쪽에서
    호출해도 되고, ``active_operations()`` 는 호출한 프로세스의 로컬 카운터다.
    """

    def __init__(
        self,
        state_file: str | Path,
        *,
        default_timeout: float = DEFAULT_DRAIN_TIMEOUT_SECONDS,
        now: NowFn | None = None,
    ) -> None:
        self._state_file = Path(state_file).expanduser()
        self._default_timeout = max(1.0, float(default_timeout))
        self._now = now or _utcnow
        # active operation 카운터 — asyncio 단일 스레드 가정이라 락 없이 int 로 충분.
        self._active_count = 0

    @property
    def state_file(self) -> Path:
        return self._state_file

    # -- drain 요청/해제 -------------------------------------------------

    def request_drain(
        self,
        reason: str,
        timeout: float | None = None,
        *,
        source: str = "",
    ) -> DrainState:
        """drain 을 요청한다 — deadline 은 now + timeout.

        기존 drain 요청이 있으면 새 요청으로 덮어쓴다(마지막 요청 우선 —
        deploy 재시도 시 이전 deadline 이 새 창을 조기 만료시키면 안 된다).
        """
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("drain reason 은 비어 있을 수 없습니다.")
        effective_timeout = (
            self._default_timeout if timeout is None else max(1.0, float(timeout))
        )
        now = self._now()
        state = DrainState(
            draining=True,
            reason=clean_reason,
            requested_at=now.isoformat(),
            deadline=(now + timedelta(seconds=effective_timeout)).isoformat(),
            source=str(source or "").strip(),
        )
        self._write_state(state)
        logger.info(
            "Drain requested: reason=%s timeout=%.0fs deadline=%s",
            clean_reason,
            effective_timeout,
            state.deadline,
        )
        return state

    def _write_state(self, state: DrainState) -> None:
        """tmp 파일에 쓰고 rename 으로 교체하는 원자적 저장.

        관찰자(bot 프로세스)가 절대 half-written JSON 을 읽지 않게 한다.
        """
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp.replace(self._state_file)

    def clear_drain(self) -> None:
        """drain 요청을 해제한다 — 파일이 없어도 조용히 성공(멱등)."""
        try:
            self._state_file.unlink(missing_ok=True)
        except OSError as exc:
            # 해제 실패는 deadline 자동 만료가 안전망이므로 예외 대신 경고만.
            logger.warning("Failed to clear drain state %s: %s", self._state_file, exc)
            return
        logger.info("Drain cleared: %s", self._state_file)

    # -- 상태 조회 --------------------------------------------------------

    def state(self) -> DrainState:
        """현재 drain 상태 스냅샷 — deadline 경과 시 idle 로 간주한다."""
        if not self._state_file.is_file():
            return DrainState.idle()
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            # fail-open — 깨진 drain 파일이 서비스 intake 전체를 막으면 안 된다.
            logger.warning(
                "Ignoring malformed drain state file %s: %s", self._state_file, exc
            )
            return DrainState.idle()
        if not isinstance(raw, dict) or not bool(raw.get("draining")):
            return DrainState.idle()
        deadline = _parse_iso(raw.get("deadline"))
        # deadline 이 없거나 파싱 불가한 요청은 만료 안전망이 없으므로 무시한다.
        if deadline is None or deadline <= self._now():
            return DrainState.idle()
        return DrainState(
            draining=True,
            reason=str(raw.get("reason") or ""),
            requested_at=(str(raw["requested_at"]) if raw.get("requested_at") else None),
            deadline=deadline.isoformat(),
            source=str(raw.get("source") or ""),
        )

    def is_draining(self) -> bool:
        """유효한(만료 전) drain 요청이 있으면 True."""
        return self.state().draining

    def maintenance_notice(self) -> str | None:
        """drain 중이면 사용자-facing 안내 문구, 아니면 None.

        채널(텔레그램 등)이 이 callable 하나만 주입받아 정책과 문구를 함께
        가져가도록 한 헬퍼.
        """
        return DRAIN_MAINTENANCE_MESSAGE if self.is_draining() else None

    def status(self) -> dict:
        """admin health 응답용 상태 dict — drain 상태 + active operation 수."""
        snapshot = self.state().to_dict()
        snapshot["active_operations"] = self._active_count
        return snapshot

    # -- active operation 추적 -------------------------------------------

    def begin_operation(self, name: str = "turn") -> None:
        """실행 중 operation 을 1 증가시킨다."""
        self._active_count += 1
        logger.debug("Drain operation begin: %s (active=%d)", name, self._active_count)

    def end_operation(self, name: str = "turn") -> None:
        """실행 중 operation 을 1 감소시킨다 — 0 밑으로는 내려가지 않는다."""
        # 짝이 안 맞는 end 호출이 카운터를 음수로 만들면 이후 begin/end 가
        # 정상이어도 drain 폴링이 "이미 0"으로 오판한다 — 바닥을 0 으로 고정.
        self._active_count = max(0, self._active_count - 1)
        logger.debug("Drain operation end: %s (active=%d)", name, self._active_count)

    def active_operations(self) -> int:
        """현재 실행 중 operation 수(이 프로세스 기준)."""
        return self._active_count

    @contextmanager
    def operation(self, name: str = "turn") -> Iterator[None]:
        """``with`` 블록 동안 operation 을 추적하는 컨텍스트 매니저."""
        self.begin_operation(name)
        try:
            yield
        finally:
            self.end_operation(name)


__all__ = [
    "DEFAULT_DRAIN_TIMEOUT_SECONDS",
    "DRAIN_CRON_SKIPPED_MESSAGE",
    "DRAIN_MAINTENANCE_MESSAGE",
    "DrainController",
    "DrainState",
]
