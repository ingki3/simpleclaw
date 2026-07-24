"""DreamingPipeline에서 분리한 단계별 service 함수.

이 모듈의 함수들은 ``DreamingPipeline`` 인스턴스 메서드로 바인딩된다.
기존 public surface와 사용자 데이터 schema를 유지하기 위해 동작 코드는 원본에서
보수적으로 이동만 하고, 의존성은 dreaming 모듈의 기존 전역을 재사용한다.
"""

from __future__ import annotations

from simpleclaw.memory import dreaming as _dreaming
from simpleclaw.memory.dreaming import *

AUTO_TRIGGER_MODE_DOWNWEIGHT = _dreaming.AUTO_TRIGGER_MODE_DOWNWEIGHT
AUTO_TRIGGER_MODE_EXCLUDE = _dreaming.AUTO_TRIGGER_MODE_EXCLUDE
_CLUSTER_MARKER_END = _dreaming._CLUSTER_MARKER_END
_CLUSTER_MARKER_START = _dreaming._CLUSTER_MARKER_START
_CLUSTER_SECTION_RE = _dreaming._CLUSTER_SECTION_RE
_VALID_AUTO_TRIGGER_MODES = _dreaming._VALID_AUTO_TRIGGER_MODES
_coerce_meta_items = _dreaming._coerce_meta_items
logger = _dreaming.logger
json = _dreaming.json
re = _dreaming.re
shutil = _dreaming.shutil
time = _dreaming.time
datetime = _dreaming.datetime
timedelta = _dreaming.timedelta

def is_active_projects_enabled(self) -> bool:
    """active-projects 추출/갱신이 활성화되어 있는지 여부.

    ``user_file`` + ``active_projects_file`` 모두 설정된 경우에만 활성화. 둘 중
    하나라도 누락이면 본 사이클에서 active-projects 단계는 통째로 건너뛴다
    (silently — 기존 테스트와 운영 환경에 영향 없음).
    """
    return self._user_file is not None and self._active_projects_file is not None

def update_active_projects(
    self,
    observations: list[dict],
    *,
    now: datetime | None = None,
) -> list[ActiveProject]:
    """LLM이 추출한 프로젝트 관측치로 sidecar와 USER.md 섹션을 갱신한다.

    흐름:
    1. sidecar 로드 (없으면 빈 dict).
    2. ``observations`` 를 ``ActiveProject`` 로 변환 후 ``merge_projects`` 로 병합
       (last_seen=now, first_seen 보존).
    3. 갱신된 sidecar 전량 저장.
    4. 윈도우 내 프로젝트만 골라 USER.md의 ``active-projects`` 섹션 본문을 교체.

    Args:
        observations: LLM 응답의 ``active_projects`` 리스트. 각 항목은
            ``{"name": str, "role": str, "recent_summary": str}`` 형태.
        now: 명시적 시각(테스트 결정성을 위해). None이면 ``datetime.now()``.

    Returns:
        이번 사이클 종료 후 USER.md 섹션에 렌더링된 활성 프로젝트 리스트
        (윈도우 내, last_seen 내림차순). 호출자가 결과 컨텍스트에서 활용 가능.

    Raises:
        ProtectedSectionError: USER.md에 active-projects managed 섹션이 없거나
            마커가 잘못된 경우. 호출자(``run()``)가 fail-closed로 처리한다.
    """
    if not self.is_active_projects_enabled():
        return []

    ts = now or datetime.now()
    store = ActiveProjectStore(self._active_projects_file)
    existing = store.load()

    new_observations: list[ActiveProject] = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        name = str(obs.get("name", "")).strip()
        if not name:
            continue
        new_observations.append(
            ActiveProject(
                name=name,
                role=str(obs.get("role", "")).strip(),
                recent_summary=str(obs.get("recent_summary", "")).strip(),
                first_seen=ts,
                last_seen=ts,
            )
        )

    merged = merge_projects(existing, new_observations, now=ts)

    # 관측 자체가 비어 있어도 매 사이클 USER.md를 다시 렌더링한다 — 그래야 윈도우
    # 외로 빠진 항목이 적시에 섹션에서 사라지고, 사용자가 보는 표시가 자동으로 최신.
    store.save_all(merged)

    active = filter_active(merged, self._active_projects_window_days, now=ts)
    body = render_active_projects_body(active)

    existing_text = self._user_file.read_text(encoding="utf-8")
    new_text = replace_section_body(
        existing_text, self._active_projects_section, body
    )
    if new_text != existing_text:
        self._user_file.write_text(new_text, encoding="utf-8")
        logger.info(
            "Refreshed active-projects section in %s (%d active project(s))",
            self._user_file,
            len(active),
        )
    return active

