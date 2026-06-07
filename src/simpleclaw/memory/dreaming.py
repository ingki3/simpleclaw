# ruff: noqa: F401
"""드리밍 파이프라인: 대화 이력을 요약하여 핵심 기억(MEMORY.md)과 사용자 프로필(USER.md)을 갱신하는 모듈.

주요 동작 흐름:
1. run() 호출 시 기존 MEMORY.md / USER.md를 백업(.bak)한다.
2. 마지막 드리밍 이후 미처리 대화 메시지를 수집한다.
3. LLM에게 대화를 분석시켜 기억 요약(memory)과 사용자 인사이트(user_insights)를 추출한다.
4. 결과를 각각 MEMORY.md, USER.md의 managed 영역(BIZ-72 Protected Section)에 append한다.

Phase 3(spec 005): 클러스터링이 활성화되면 MEMORY.md는 시간순 append가 아니라
 클러스터별 ``<!-- cluster:N start --> ... <!-- cluster:N end -->`` 섹션 단위로 upsert된다.
임베딩이 부착된 메시지를 ``IncrementalClusterer``로 그룹핑하고, 영향받은 클러스터마다
LLM에 (기존 요약 + 신규 메시지)를 보내 새 요약을 받아 ``semantic_clusters`` 테이블과
MEMORY.md를 함께 갱신한다. USER/SOUL/AGENT 파일은 기존 동작 그대로 유지된다.

BIZ-72 Protected Section 모델:
드리밍은 다음 managed 마커 안쪽 영역에만 쓸 수 있다. 외부(정체성, 캘린더 매핑 등)는
read-only로 보존된다.
    ``<!-- managed:dreaming:<section> -->`` ... ``<!-- /managed:dreaming:<section> -->``
파일별 기본 섹션 이름은 ``DEFAULT_SECTIONS`` dict에 정의되어 있다. 마커가 누락된
파일에 대한 쓰기는 fail-closed(전체 사이클 abort, 기존 파일 보존)로 처리된다.

설계 결정:
- LLM 호출 실패 시 단순 텍스트 요약(fallback)으로 대체하여 파이프라인이 중단되지 않도록 한다.
- 대화 텍스트는 8000자로 잘라 LLM 컨텍스트 초과를 방지한다.
- 백업 파일명에 타임스탬프를 포함하여 여러 번 드리밍해도 이전 백업이 덮어씌워지지 않는다.
- 클러스터링이 비활성이거나 임베딩이 전혀 없는 입력일 때는 기존 append 동작으로 자연 fallback 한다.
- Protected Section 위반은 fail-closed: 한 파일이라도 markers가 없거나 잘못돼 있으면
  전체 사이클을 중단하고 어느 파일도 변경하지 않는다(부분 변경의 위험을 제거).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from simpleclaw.memory.active_projects import (
    ActiveProject,
    ActiveProjectStore,
    filter_active,
    merge_projects,
    render_section_body as render_active_projects_body,
)
from simpleclaw.memory.agent_update_filter import filter_agent_updates_with_stats
from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.insights import (
    InsightMeta,
    InsightStore,
    is_promoted,
    merge_insights,
    normalize_topic,
)
from simpleclaw.memory.language_policy import (
    LanguagePolicy,
    filter_active_projects,
    filter_meta_items,
    filter_text_to_primary,
    language_instruction_block,
)
from simpleclaw.memory.models import (
    ClusterRecord,
    ConversationMessage,
    MemoryEntry,
    is_auto_trigger_channel,
)
from simpleclaw.memory.memory_items_sync import (
    sync_active_projects_to_memory_items,
    sync_cluster_summary_to_memory_item,
    sync_insights_to_memory_items,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.prompt_loader import load_dreaming_prompt
from simpleclaw.memory.protected_section import (
    ProtectedSectionError,
    ProtectedSectionMissing,
    append_to_section,
    get_section_body,
    has_managed_section,
    replace_section_body,
)
from simpleclaw.memory.safety_backup import (
    SafetyBackupManager,
    find_legacy_memory_backup,
)
from simpleclaw.memory.dreaming_runs import (
    SKIP_EMPTY_RESULTS,
    SKIP_MIDWRITE_ABORTED,
    SKIP_NO_MESSAGES,
    SKIP_PREFLIGHT_FAILED,
    DreamingRunStore,
)
from simpleclaw.memory.reject_blocklist import RejectBlocklistStore
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionStore,
)
from simpleclaw.proactive.models import ProactiveOpportunity
from simpleclaw.proactive.store import OpportunityStore

logger = logging.getLogger(__name__)

# BIZ-72 — 파일별 기본 managed 섹션 이름.
#
# 드리밍은 이 이름의 마커 안쪽에만 쓴다. 마커가 없으면 fail-closed.
# 운영자가 다른 섹션 이름을 쓰고 싶으면 ``DreamingPipeline`` 생성 시 override 가능
# (예: ``memory_section_name="custom"``). 기본값은 SimpleClaw 표준 템플릿과 일치.
DEFAULT_MEMORY_SECTION = "journal"          # MEMORY.md — 시간순 dreaming 기록
DEFAULT_CLUSTER_SECTION = "clusters"        # MEMORY.md — Phase 3 cluster 섹션 컨테이너
DEFAULT_USER_SECTION = "insights"           # USER.md — dreaming-derived insights
DEFAULT_SOUL_SECTION = "dreaming-updates"   # SOUL.md — dreaming-suggested 변경
DEFAULT_AGENT_SECTION = "dreaming-updates"  # AGENT.md — dreaming-suggested 변경
# BIZ-78 — USER.md 의 archive 섹션. decay 된 인사이트의 *흔적* (date-stamped 목록) 만 남고
# 원본 메타는 sidecar 에서 ``archived_at`` 으로 표현된다. 이 섹션은 *선택적* — 마커가
# 없으면 archive 가 markdown 에 노출되지 않을 뿐 sidecar 의 archived_at 갱신은 그대로
# 진행된다(기존 USER.md 가 깨지지 않도록).
DEFAULT_ARCHIVE_SECTION = "archive"

# BIZ-74 — Active Projects 섹션 기본 이름. USER.md 안의 별도 managed 섹션으로 운영.
# 매 dreaming 사이클에 in-place 갱신되며, 마커 외부는 BIZ-72 가드로 자동 보호된다.
DEFAULT_ACTIVE_PROJECTS_SECTION = "active-projects"

# 활성 윈도우 기본값(일). config 로 노출. 7일은 "사용자가 한 주 동안 만진 프로젝트"
# 라는 직관과 일치하며, 5-01~5-03 SimpleClaw/Multica 트랙처럼 며칠 집중하다
# 다른 일로 옮겨가는 패턴을 자연스럽게 포착한다.
DEFAULT_ACTIVE_PROJECTS_WINDOW_DAYS = 7


# BIZ-76 — 자동 트리거(cron/recipe) 메시지의 코퍼스 처리 모드.
#
# - ``"exclude"`` (기본): 코퍼스에서 완전히 제거. 자동 트리거 메시지는 dreaming
#   분석 입력에 들어가지 않으므로 인사이트로 일반화될 위험 자체가 사라진다.
#   "auto-trigger 만 있는 코퍼스 → 인사이트 0건" 의 강한 가드를 만든다.
# - ``"downweight"``: 코퍼스에 일부만 남긴다(deterministic stride sampling).
#   ``auto_trigger_weight`` 로 비율을 정한다(0.2~0.3 권장). organic 메시지가
#   풍부하고 자동 트리거가 보조적 신호로 유용한 환경에서 선택.
# - ``"include"``: 어떤 가공도 하지 않고 코퍼스에 그대로 둔다(레거시 호환).
#
# 기본값을 ``"exclude"`` 로 두는 이유: 부모 BIZ-66 §2-6 사례(정치/AI 트렌드
# 자동 발사가 "지속적 관심" 으로 일반화) 가 시연하듯, 자동 트리거를 organic
# 발화와 동등하게 다루는 것은 사용자 의도 정합성에 직접적 위해를 끼친다.
# fail-closed 로 시작해 운영자가 옵트인할 때 약화시킨다.
AUTO_TRIGGER_MODE_EXCLUDE = "exclude"
AUTO_TRIGGER_MODE_DOWNWEIGHT = "downweight"
AUTO_TRIGGER_MODE_INCLUDE = "include"
_VALID_AUTO_TRIGGER_MODES = frozenset({
    AUTO_TRIGGER_MODE_EXCLUDE,
    AUTO_TRIGGER_MODE_DOWNWEIGHT,
    AUTO_TRIGGER_MODE_INCLUDE,
})


# BIZ-301 — dreaming 프롬프트는 레포 루트 ``prompts/dreaming/{name}.yaml`` 단일
# SoT 에서 관리된다 (BIZ-298 의 운영자 override / 패키지 fallback 2단 폐지).
# ``prompt_loader.load_dreaming_prompt(name)`` 가 ``SIMPLECLAW_ROOT`` env → 모듈
# 위치에서 ``pyproject.toml`` walk-up 순으로 root 를 해소한다.
#
# BIZ-80: ``{language_instruction}`` placeholder 는 정책이 비활성(primary=None) 이면
# 빈 문자열로 대체되어 프롬프트에 어떤 언어 강제도 들어가지 않는다(레거시 호환).
# 정책이 활성이면 출력 본문이 어떤 언어로 적혀야 하는지(파일별 override 포함)
# 명시된다.


def _coerce_meta_items(raw: object) -> list[dict]:
    """LLM 이 반환한 ``user_insights_meta`` 를 정상화한다 (BIZ-299).

    형식이 맞지 않는 항목은 silently drop — 한 항목이 잘못됐다고 전체 메타를
    잃는 것은 다른 dreaming 산출물까지 못 보게 만든다. ``None`` 입력은 빈 리스트.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if (
            isinstance(item, dict)
            and isinstance(item.get("topic"), str)
            and isinstance(item.get("text"), str)
        ):
            out.append({"topic": item["topic"], "text": item["text"]})
    return out


# Phase 3 — MEMORY.md 클러스터 섹션 마커. 정규식이 아닌 단순 문자열 식별자로 검색.
_CLUSTER_MARKER_START = "<!-- cluster:{cid} start -->"
_CLUSTER_MARKER_END = "<!-- cluster:{cid} end -->"
# 마커 인식용 정규식 — 시작/끝 마커와 cluster_id를 캡처
_CLUSTER_SECTION_RE = re.compile(
    r"<!-- cluster:(\d+) start -->\n?(.*?)\n?<!-- cluster:\1 end -->",
    re.DOTALL,
)


class DreamingPipeline:
    """대화 이력을 분석하여 MEMORY.md, USER.md, SOUL.md, AGENT.md를 갱신하는 파이프라인.

    LLM을 사용해 대화를 분석하고, 각 파일의 역할에 맞는 정보를 추출·갱신한다.
    파일 수정 전 memory-backup/ 폴더에 .bak 백업을 생성하여 데이터 손실을 방지한다.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        memory_file: str | Path,
        user_file: str | Path | None = None,
        soul_file: str | Path | None = None,
        agent_file: str | Path | None = None,
        llm_router=None,
        dreaming_model: str = "",
        clusterer: IncrementalClusterer | None = None,
        enable_clusters: bool = False,
        *,
        memory_section: str = DEFAULT_MEMORY_SECTION,
        cluster_section: str = DEFAULT_CLUSTER_SECTION,
        user_section: str = DEFAULT_USER_SECTION,
        soul_section: str = DEFAULT_SOUL_SECTION,
        agent_section: str = DEFAULT_AGENT_SECTION,
        archive_section: str = DEFAULT_ARCHIVE_SECTION,
        insights_file: str | Path | None = None,
        insight_promotion_threshold: int = 3,
        auto_trigger_mode: str = AUTO_TRIGGER_MODE_EXCLUDE,
        auto_trigger_weight: float = 0.3,
        reject_blocklist_file: str | Path | None = None,
        decay_archive_after_days: int | None = 30,
        reject_default_ttl_days: int | None = None,
        suggestions_file: str | Path | None = None,
        blocklist_file: str | Path | None = None,
        auto_promote_confidence: float = 0.7,
        auto_promote_evidence_count: int | None = None,
        active_projects_file: str | Path | None = None,
        active_projects_section: str = DEFAULT_ACTIVE_PROJECTS_SECTION,
        active_projects_window_days: int = DEFAULT_ACTIVE_PROJECTS_WINDOW_DAYS,
        runs_file: str | Path | None = None,
        runs_max_records: int = DreamingRunStore.DEFAULT_MAX_RECORDS,
        language_policy: LanguagePolicy | None = None,
        safety_backup_manager: SafetyBackupManager | None = None,
        memory_backup_dir: str | Path | None = None,
        max_tokens: dict[str, int | None] | None = None,
        proactive_extractor=None,
        opportunity_store: OpportunityStore | None = None,
    ) -> None:
        """드리밍 파이프라인을 초기화한다.

        Args:
            conversation_store: 대화 이력 저장소 인스턴스.
            memory_file: MEMORY.md 파일 경로.
            user_file: USER.md 파일 경로. None이면 사용자 인사이트를 저장하지 않는다.
            soul_file: SOUL.md 파일 경로. None이면 성격/말투 갱신을 하지 않는다.
            agent_file: AGENT.md 파일 경로. None이면 행동 규칙 갱신을 하지 않는다.
            llm_router: LLM 호출을 위한 라우터. None이면 폴백 요약을 사용한다.
            dreaming_model: 드리밍에 사용할 LLM 모델명. 빈 문자열이면 라우터 기본값 사용.
            clusterer: ``IncrementalClusterer`` 인스턴스. ``enable_clusters=True``일 때만 사용된다.
            enable_clusters: True면 Phase 3 그래프형 드리밍(클러스터 기반 MEMORY.md upsert) 사용.
                False면 기존 append 동작 유지(하위 호환).
            memory_section: MEMORY.md 시간순 append용 managed 섹션 이름. 기본 ``journal``.
            cluster_section: MEMORY.md cluster 컨테이너 managed 섹션 이름. 기본 ``clusters``.
            user_section: USER.md insight append용 섹션 이름. 기본 ``insights``.
            soul_section: SOUL.md dreaming 변경용 섹션 이름. 기본 ``dreaming-updates``.
            agent_section: AGENT.md dreaming 변경용 섹션 이름. 기본 ``dreaming-updates``.
            insights_file: 인사이트 메타 sidecar(JSONL) 파일 경로 (BIZ-73). None이면
                ``user_file`` 옆 ``insights.jsonl`` 로 자동 결정. ``user_file``도 None이면
                인사이트 메타 추적은 비활성.
            insight_promotion_threshold: 인사이트 승격 임계 관측 횟수 (BIZ-73). 단발 관측은
                항상 confidence ≤ 0.4 로 캡되고, 이 횟수에 도달해야 승격선(0.7)에 진입한다.
                기본 3회.
            auto_trigger_mode: 자동 트리거(cron/recipe) 메시지의 코퍼스 처리 모드 (BIZ-76).
                ``"exclude"`` (기본) — 코퍼스에서 완전히 제거. ``"downweight"`` —
                ``auto_trigger_weight`` 비율만 stride sampling 으로 보존.
                ``"include"`` — 가공 없이 그대로 통과(레거시 호환).
            auto_trigger_weight: ``auto_trigger_mode="downweight"`` 일 때 자동 트리거
                메시지를 보존할 비율 (0.0 ~ 1.0). 기본 0.3. 0.2~0.3 권장(부모 BIZ-66 §3-E).
                ``mode != "downweight"`` 면 무시된다.
            archive_section: USER.md 의 decay archive managed 섹션 이름 (BIZ-78). 기본
                ``archive``. 이 섹션이 USER.md 에 없으면 decay 는 markdown 에 흔적을
                남기지 않고 sidecar 의 ``archived_at`` 만 갱신된다(기존 파일과 호환).
            reject_blocklist_file: 거부된 topic 의 차단 리스트 sidecar (BIZ-78). None 이면
                ``insights_file`` 옆 ``rejects.jsonl`` 로 자동 결정. 인사이트 sidecar 가
                비활성이면 차단 리스트도 비활성.
            decay_archive_after_days: ``last_seen`` 으로부터 이 일수 이상 reinforcement
                가 없으면 archive 처리한다 (BIZ-78). ``None`` 이면 decay 비활성.
                기본 30일.
            reject_default_ttl_days: ``register_reject`` 호출 시 ttl 인자 미지정 시
                사용할 기본 TTL(일). ``None`` 이면 영구 차단(가장 흔한 케이스). 기본
                ``None``.
            suggestions_file: BIZ-79 — pending suggestion sidecar(JSONL) 경로. 지정되면
                "dry-run + admin review" 모드가 활성화되어 추출된 인사이트가 USER.md 에
                즉시 쓰이지 않고 review 큐에 적재된다 (auto-promote 임계치를 동시에
                충족한 항목만 큐를 우회). None 이면 레거시 동작(추출 즉시 USER.md
                bullet append) 유지.
            blocklist_file: BIZ-79 — reject 시 누적되는 토픽 블록리스트 sidecar 경로.
                None 이면 블록리스트 기능 비활성. ``suggestions_file`` 가 지정될 때
                보통 함께 지정한다 — 그래야 reject → 재추출 차단 루프가 닫힌다.
            auto_promote_confidence: BIZ-79 — 큐를 우회해 즉시 USER.md 에 적용할
                confidence 하한 (기본 0.7 = 승격선). 이 값과 ``auto_promote_evidence_count``
                를 **동시에** 만족해야 자동 적용된다. 한쪽만 만족하면 큐로 보낸다.
            auto_promote_evidence_count: BIZ-79 — 자동 적용에 필요한 evidence_count
                하한. ``None`` 이면 ``insight_promotion_threshold`` 와 같은 값을 사용한다.
                "단발 고신뢰" 가짜 일반화를 막기 위해 confidence 만이 아니라 누적 관측
                수도 함께 본다.
            active_projects_file: active-projects sidecar JSONL 경로 (BIZ-74).
                ``None``이면 active-projects 추출/갱신 자체를 비활성화��다 — 기존
                테스트·운영 환경 호환성 보장. ``user_file``과 함께 설정되어야 의미가 있다.
            active_projects_section: USER.md 내 active-projects managed 섹션 이름.
                기본 ``active-projects``.
            active_projects_window_days: 활성 윈도우(일). 윈도우 외 sidecar 항목은
                USER.md 섹션에서 자동으로 사라지지만 sidecar에는 보관된다.
                기본 7일.
            language_policy: BIZ-80 1차 언어 정책 (USER/MEMORY/AGENT 등 dreaming-managed
                섹션의 출력 언어). ``None`` 이면 기본값(``LanguagePolicy()`` — 한국어)을
                사용하므로 영어 입력에서도 USER.md 인사이트가 한국어로 적힌다. 정책의
                ``primary`` 를 ``None`` 으로 두면 검사를 끄고 출력을 그대로 통과시킨다
                (레거시 호환). 정책은 (1) dreaming 프롬프트에 1차 언어 강제 지시문을
                추가하고, (2) 추출된 결과(memory bullet, user_insights, meta items,
                active_projects role/recent_summary, soul/agent updates)에서 비-1차
                언어 항목을 자동 드롭한다.
            proactive_extractor: BIZ-333 — Dreaming 코퍼스에서 proactive 후보를
                생성하는 optional hook. None 이면 후보 추출을 건너뛰어 레거시 호환.
            opportunity_store: BIZ-333 — 생성 후보를 pending queue에 upsert하는 저장소.
                None 이면 hook이 있어도 저장하지 않는다.

        BIZ-72: 모든 dreaming 쓰기는 위 managed 섹션 마커 안쪽으로만 이뤄지며,
        마커가 없는 파일은 fail-closed로 처리된다(쓰기 시도 시 abort, 파일 보존).
        """
        self._store = conversation_store
        self._memory_file = Path(memory_file)
        self._user_file = Path(user_file) if user_file else None
        self._soul_file = Path(soul_file) if soul_file else None
        self._agent_file = Path(agent_file) if agent_file else None
        self._router = llm_router
        self._dreaming_model = dreaming_model or None
        # Phase 3: 클러스터링이 None이면 enable_clusters 요청도 무시(안전 폴백)
        self._clusterer = clusterer
        self._enable_clusters = bool(enable_clusters and clusterer is not None)
        # BIZ-72: 파일별 managed 섹션 이름. 운영자가 override 가능.
        self._memory_section = memory_section
        self._cluster_section = cluster_section
        self._user_section = user_section
        self._soul_section = soul_section
        self._agent_section = agent_section
        self._archive_section = archive_section

        # BIZ-73: insight 메타 sidecar.
        # - 명시 경로가 없으면 USER.md 옆에 둔다 (운영자 수기 검토가 쉬움).
        # - USER.md 도 없으면 메타 추적 자체를 끈다(인사이트의 사람이 읽는 출력 자리가 없으므로).
        if insights_file is not None:
            self._insights_store: InsightStore | None = InsightStore(insights_file)
        elif self._user_file is not None:
            self._insights_store = InsightStore(
                self._user_file.parent / "insights.jsonl"
            )
        else:
            self._insights_store = None
        self._insight_promotion_threshold = max(1, int(insight_promotion_threshold))

        # BIZ-76 — 자동 트리거 코퍼스 분리. 알 수 없는 모드는 안전 기본값(exclude)
        # 으로 폴백하여 운영자가 오타로 필터를 무력화하는 사고를 막는다.
        if auto_trigger_mode not in _VALID_AUTO_TRIGGER_MODES:
            logger.warning(
                "Unknown auto_trigger_mode '%s', falling back to 'exclude'",
                auto_trigger_mode,
            )
            auto_trigger_mode = AUTO_TRIGGER_MODE_EXCLUDE
        self._auto_trigger_mode = auto_trigger_mode
        # 가중치는 [0, 1] 범위로 클램프. 1.0 이면 사실상 include 와 동일.
        self._auto_trigger_weight = max(0.0, min(1.0, float(auto_trigger_weight)))

        # BIZ-78: reject 차단 리스트 sidecar 와 decay 정책.
        # - 인사이트 sidecar 가 비활성이면 차단 리스트도 비활성(둘은 짝을 이룬다 — 차단할
        #   인사이트 자체가 없는데 reject 만 따로 저장하는 건 무의미).
        # - 차단 리스트 경로는 ``reject_blocklist_file`` 인자 우선, 없으면 insights 옆
        #   ``rejects.jsonl`` (운영자가 두 파일을 한 디렉토리에서 보게 됨).
        if self._insights_store is None:
            self._reject_store: RejectBlocklistStore | None = None
        elif reject_blocklist_file is not None:
            self._reject_store = RejectBlocklistStore(reject_blocklist_file)
        else:
            self._reject_store = RejectBlocklistStore(
                self._insights_store.path.parent / "rejects.jsonl"
            )
        # decay_archive_after_days = None 이면 decay 비활성 — apply_decay 가 no-op.
        self._decay_archive_after_days = (
            int(decay_archive_after_days)
            if decay_archive_after_days is not None
            else None
        )
        self._reject_default_ttl_days = (
            int(reject_default_ttl_days)
            if reject_default_ttl_days is not None
            else None
        )

        # BIZ-79: pending suggestion 큐 + reject 블록리스트.
        # - suggestions_file 가 None 이면 큐가 꺼져 있어 추출된 인사이트는 즉시
        #   USER.md 에 적용된다(레거시 호환).
        # - blocklist_file 가 None 이면 차단 기능이 꺼져 있어 reject 후에도 같은
        #   topic 이 다음 사이클에서 다시 추출될 수 있다 — 통합 테스트는 두 가지를
        #   함께 켜서 reject → 재추출 차단 루프를 검증한다.
        self._suggestion_store: SuggestionStore | None = (
            SuggestionStore(suggestions_file) if suggestions_file else None
        )
        self._blocklist_store: BlocklistStore | None = (
            BlocklistStore(blocklist_file) if blocklist_file else None
        )
        self._auto_promote_confidence = float(auto_promote_confidence)
        self._auto_promote_evidence_count = (
            int(auto_promote_evidence_count)
            if auto_promote_evidence_count is not None
            else self._insight_promotion_threshold
        )

        # BIZ-74: active-projects 갱신은 user_file + active_projects_file 둘 다
        # 설정되었을 때만 활성화된다. 둘 중 하나라도 없으면 추출/갱���을 건너뛴다.
        self._active_projects_file = (
            Path(active_projects_file) if active_projects_file else None
        )
        self._active_projects_section = active_projects_section
        self._active_projects_window_days = active_projects_window_days

        # BIZ-81: 드리밍 사이클 메트릭 sidecar.
        # - runs_file 가 None 이면 메트릭 기록을 건너뛴다(테스트/레거시 호환).
        # - 운영(scripts/run_bot.py)에서는 항상 주입한다 — Admin UI 의 KPI/진단 입력원.
        self._runs_store: DreamingRunStore | None = (
            DreamingRunStore(runs_file, max_records=runs_max_records)
            if runs_file
            else None
        )
        # 한 회차 내에서 apply_insight_meta 가 차단·필터한 관측 수.
        # run() 이 메트릭에 기록하기 위해 사용. apply_insight_meta 호출 직전 0으로 초기화.
        self._last_rejected_count: int = 0

        # BIZ-80: 1차 언어 정책. ``None`` (기본) 이면 enforcement 없이 LLM 출력을
        # 그대로 통과시켜 기존 테스트/배포 fixture 가 그대로 동작한다 (BIZ-80 이전
        # 동작과 byte-for-byte 동일). 운영 환경 (``scripts/run_bot.py``) 은 명시적으로
        # ``LanguagePolicy()`` (primary=ko) 를 주입해 enforcement 를 켠다.
        self._language_policy = (
            language_policy if language_policy is not None
            else LanguagePolicy(primary=None)
        )

        # BIZ-132 — 사이클 직전 safety backup. 매니저가 주입되면 ``run()`` 의 preflight
        # 직전에 ``snapshot()`` 을 호출하여 위험 파일 목록을 ``.agent/_safety_backup``
        # 디렉터리에 보존한다. 주입되지 않으면 비활성(레거시 호환).
        self._safety_backup_manager = safety_backup_manager
        # Phase 2 자가 복원이 폴백으로 참조하는 레거시 .bak 백업 디렉터리. 명시되지
        # 않으면 ``self._memory_file`` 옆 ``memory-backup/`` 으로 자동 결정 — 이는
        # ``create_backup`` 이 사용하는 위치와 일치하므로 자가 복원 후보가 자동으로
        # 정렬된다.
        if memory_backup_dir is not None:
            self._memory_backup_dir: Path = Path(memory_backup_dir)
        else:
            self._memory_backup_dir = self._memory_file.parent / "memory-backup"
        # 한 회차 내에서 self-restore 1회 한정 가드 — preflight 가 두 번째 호출돼도
        # 자가 복원을 재시도하지 않게 한다. ``run()`` 진입 시 0으로 초기화된다.
        self._self_restore_count_in_cycle: int = 0
        # 자가 복원이 실제로 일어났을 때의 메타(파일 → 사용된 백업 경로). run() 이
        # ``dreaming_runs.jsonl`` 의 ``details["recovered_from"]`` 에 기록한다.
        self._last_recovered_files: dict[str, str] = {}

        # BIZ-299 — 파일별 출력 토큰 cap. None / 빠진 키는 프로바이더 기본값으로
        # 회귀 (Claude 4096 등). 값은 ``LLMRequest.max_tokens`` (BIZ-297) 로 전달.
        # 키: ``memory`` / ``user`` / ``soul`` / ``agent`` / ``active_projects`` / ``cluster``.
        # ``active_projects`` 키가 빠지면 ``user`` 캡으로 떨어진다(USER.md 산출물이라 의미상 동일).
        self._max_tokens: dict[str, int | None] = dict(max_tokens or {})

        # BIZ-333 — Dreaming은 후보 생성까지만 수행한다. 실제 발송/cron 생성은
        # presenter/action executor 책임이므로 여기서는 pending queue upsert 외 부작용이 없다.
        self._proactive_extractor = proactive_extractor
        self._opportunity_store = opportunity_store

        # BIZ-299 — 한 회차의 파일별 LLM 호출 메트릭(duration_ms, 토큰 사용량 등).
        # ``run()`` 시점에 초기화되고, 정상/예외 종료 직전에 ``run_record.details``
        # 에 ``per_file`` 키로 합쳐진다. 운영자가 Admin UI 에서 어느 호출이 느렸는지
        # 또는 토큰을 많이 썼는지 한눈에 본다.
        self._per_file_metrics: dict[str, dict] = {}

# BIZ-349 — DreamingPipeline을 facade/coordinator로 유지하고, 단계별 구현은
# service modules에 둔다. 함수 객체를 클래스에 바인딩하여 기존 public/private method
# 이름과 monkeypatch 호환성을 보존한다.
from simpleclaw.memory.dreaming_active_projects import (  # noqa: E402
    is_active_projects_enabled,
    update_active_projects,
)
from simpleclaw.memory.dreaming_cluster_pipeline import (  # noqa: E402
    _call_dreaming_llm_for_key,
    _format_cluster_section_body,
    _parse_cluster_result,
    _run_cluster_pipeline,
    _summarize_cluster_fallback,
    _summarize_cluster_with_llm,
    assign_clusters_for_unprocessed,
    summarize_cluster,
    upsert_memory_section,
)
from simpleclaw.memory.dreaming_language import (  # noqa: E402
    _call_dreaming_llm,
    _enforce_language_policy,
    _extract_json_object,
    _format_conversations,
    _parse_llm_result,
    _summarize_fallback,
    summarize,
    summarize_active_projects,
    summarize_agent,
    summarize_memory,
    summarize_soul,
    summarize_user,
)
from simpleclaw.memory.dreaming_preflight import (  # noqa: E402
    _apply_auto_trigger_filter,
    _format_dated_block,
    _preflight_protected_sections,
    _read_existing,
    _restore_from_backups,
    _safe_append_in_section,
    _snapshot_per_file_metrics,
    _try_self_restore,
    append_to_memory,
    auto_promote_thresholds,
    blocklist_store,
    collect_unprocessed,
    collect_unprocessed_with_ids,
    create_backup,
    insight_store,
    runs_store,
    suggestion_store,
    update_agent_file,
    update_soul_file,
    update_user_file,
)
from simpleclaw.memory.dreaming_runner import (  # noqa: E402
    _extract_and_store_proactive_opportunities,
    _run_after_preflight,
    run,
)
from simpleclaw.memory.insight_meta import (  # noqa: E402
    _format_auto_applied_bullets,
    _meets_auto_promote,
    _safe_sync_memory_items,
    append_insight_to_user_file,
    apply_decay,
    apply_insight_meta,
    register_reject,
    reject_blocklist,
)

_DREAMING_SERVICE_METHODS = {
    "create_backup", "collect_unprocessed", "collect_unprocessed_with_ids",
    "_apply_auto_trigger_filter", "insight_store", "suggestion_store",
    "blocklist_store", "auto_promote_thresholds", "runs_store", "summarize",
    "summarize_memory", "summarize_user", "summarize_soul", "summarize_agent",
    "summarize_active_projects", "_call_dreaming_llm", "_read_existing",
    "_format_conversations", "_parse_llm_result", "_extract_json_object",
    "_enforce_language_policy", "_summarize_fallback", "append_to_memory",
    "_safe_append_in_section", "_format_dated_block", "update_user_file",
    "reject_blocklist", "apply_insight_meta", "_meets_auto_promote",
    "_format_auto_applied_bullets", "append_insight_to_user_file", "apply_decay",
    "register_reject", "_safe_sync_memory_items", "update_soul_file",
    "update_agent_file", "is_active_projects_enabled", "update_active_projects",
    "assign_clusters_for_unprocessed", "summarize_cluster",
    "_summarize_cluster_with_llm", "_call_dreaming_llm_for_key",
    "_parse_cluster_result", "_summarize_cluster_fallback", "upsert_memory_section",
    "_format_cluster_section_body", "_preflight_protected_sections",
    "_try_self_restore", "run", "_run_after_preflight",
    "_extract_and_store_proactive_opportunities", "_snapshot_per_file_metrics",
    "_restore_from_backups", "_run_cluster_pipeline",
}
_DREAMING_SERVICE_PROPERTIES = {
    "insight_store",
    "suggestion_store",
    "blocklist_store",
    "auto_promote_thresholds",
    "runs_store",
    "reject_blocklist",
}
_DREAMING_SERVICE_STATICMETHODS = {
    "_format_conversations",
    "_extract_json_object",
    "_format_auto_applied_bullets",
    "_format_cluster_section_body",
    "_restore_from_backups",
}
for _name in _DREAMING_SERVICE_METHODS:
    _func = globals()[_name]
    if _name in _DREAMING_SERVICE_PROPERTIES:
        setattr(DreamingPipeline, _name, property(_func))
    elif _name in _DREAMING_SERVICE_STATICMETHODS:
        setattr(DreamingPipeline, _name, staticmethod(_func))
    else:
        setattr(DreamingPipeline, _name, _func)

del _name, _func
