"""드리밍 트리거: 자동 드리밍 파이프라인의 실행 조건 평가기.

드리밍(Dreaming)은 사용자가 비활성 상태일 때 대화 내용을 정리·요약하는 백그라운드 작업이다.
이 모듈은 드리밍 실행 여부를 판단하고, 조건 충족 시 파이프라인을 기동한다.

실행 조건 (모두 충족해야 함):
1. 오늘 이미 드리밍을 실행하지 않았을 것 (하루 1회 제한)
2. 현재 시각이 설정된 야간 시간(overnight_hour) 이후일 것
3. 최근 사용자 입력이 idle_threshold 초 이상 경과했을 것
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from simpleclaw.daemon.store import DaemonStore
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline

logger = logging.getLogger(__name__)

LAST_DREAMING_KEY = "last_dreaming_timestamp"


class DreamingTrigger:
    """자동 드리밍 조건을 평가하고, 충족 시 파이프라인을 기동한다.

    실행 조건 (모두 충족해야 함):
    1. 마지막 사용자 입력이 idle_threshold 초 이상 경과했을 것
    2. 현재 시각이 overnight_hour 이후일 것
    3. 하루 1회만 실행 (같은 날짜에 이미 실행했으면 건너뜀)
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        dreaming_pipeline: DreamingPipeline,
        daemon_store: DaemonStore,
        overnight_hour: int = 3,
        idle_threshold: int = 7200,
    ) -> None:
        self._conv_store = conversation_store
        self._pipeline = dreaming_pipeline
        self._daemon_store = daemon_store
        self._overnight_hour = overnight_hour
        self._idle_threshold = idle_threshold

    async def should_run(self) -> bool:
        """드리밍 실행 조건이 충족되었는지 확인한다."""
        now = datetime.now()

        # 조건 1: 오늘 이미 실행했는가?
        last_dreaming = self._get_last_dreaming()
        if last_dreaming and last_dreaming.date() == now.date():
            return False

        # 조건 2: 야간 시간대에 진입했는가?
        if now.hour < self._overnight_hour:
            return False

        # 조건 3: 처리할 메시지가 존재하는가?
        messages = self._conv_store.get_recent(limit=1)
        if not messages:
            return False

        # 조건 4: 사용자가 충분히 오래 비활성 상태인가?
        last_input = messages[-1].timestamp
        if last_input is None:
            return False

        idle_seconds = (now - last_input).total_seconds()
        if idle_seconds < self._idle_threshold:
            return False

        return True

    async def execute(self) -> None:
        """드리밍 파이프라인을 실행하고 완료 시각을 기록한다."""
        logger.info("Dreaming trigger: conditions met, starting dreaming pipeline.")

        last_dreaming = self._get_last_dreaming()

        try:
            result = await self._pipeline.run(last_dreaming)
            if result:
                self._daemon_store.set_state(
                    LAST_DREAMING_KEY, datetime.now().isoformat()
                )
                logger.info("Dreaming pipeline completed: %s", result.source)
            else:
                logger.info("Dreaming pipeline: no new content to process.")
        except Exception:
            logger.exception("Dreaming pipeline failed")

    def _get_last_dreaming(self) -> datetime | None:
        """데몬 상태에서 마지막 드리밍 실행 시각을 조회한다."""
        value = self._daemon_store.get_state(LAST_DREAMING_KEY)
        if value:
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None
