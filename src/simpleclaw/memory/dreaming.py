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
from datetime import datetime, timedelta
from pathlib import Path

from simpleclaw.memory.active_projects import (
    ActiveProject,
    ActiveProjectStore,
    filter_active,
    merge_projects,
    render_section_body as render_active_projects_body,
)
from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.insights import (
    InsightMeta,
    InsightStore,
    is_promoted,
    merge_insights,
    normalize_topic,
)
from simpleclaw.memory.models import (
    ClusterRecord,
    ConversationMessage,
    MemoryEntry,
    is_auto_trigger_channel,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.protected_section import (
    ProtectedSectionError,
    ProtectedSectionMissing,
    append_to_section,
    get_section_body,
    has_managed_section,
    replace_section_body,
)
from simpleclaw.memory.reject_blocklist import RejectBlocklistStore
from simpleclaw.memory.suggestions import (
    BlocklistStore,
    SuggestionStore,
)

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


# 프롬프트는 LLM에게 마커 자체를 출력하지 말라고 명시 — 출력은 본문 마크다운만이며
# 본 모듈이 managed 섹션 안쪽으로 안전하게 append한다.
_DREAMING_PROMPT = """\
다음 대화 내역을 분석하여 다섯 가지를 JSON으로 추출하세요.

⚠️ 출력 규칙(중요): 본문은 SimpleClaw가 USER/MEMORY/AGENT/SOUL 파일의 dreaming
managed 섹션(`<!-- managed:dreaming:... -->` 마커 내부)에 append됩니다. 응답에
managed 마커 자체(`<!-- managed:dreaming:... -->`, `<!-- /managed:dreaming:... -->`)를
포함하지 마세요. 본문 텍스트만 작성하세요. 마커 외부에는 절대 쓰지 않으므로,
정체성·캘린더 매핑·디렉토리 규약 같은 보호 영역을 갱신하려 하지 마세요.

1. "memory": 오늘 있었던 사실, 이벤트, 결정 사항을 bullet point로 요약
   - 날짜 헤더 포함 (## {date} 형식)
   - 사실 기반만 (의견/추측 금지)
   - 반복되는 주제나 관심사를 기록 (패턴 파악용)

2. "user_insights": 사용자에 대해 새로 알게 된 정보 (선호도, 관심사, 습관) — 사람이 읽는 bullet 텍스트
   - 이미 알고 있는 정보(기존 USER.md 내용)는 제외
   - 추측이 아닌 대화에서 명확히 드러난 정보만
   - 민감한 개인정보(비밀번호, 금융정보)는 절대 저장하지 않음
   - 없으면 빈 문자열

3. "user_insights_meta": 위 user_insights를 구조화한 객체 배열 (BIZ-73 — 누적 evidence 추적용)
   - 각 항목 형식: {{"topic": "<3~10자 짧은 한국어 주제 키>", "text": "<bullet 한 줄>"}}
   - topic 은 같은 주제로 다음 회차에 다시 관측될 때 매칭하기 위한 키 — 짧고 일관되게.
     예: "맥북에어 가격 조회" → topic="맥북에어가격", "정치 뉴스 요약" → topic="정치뉴스".
   - text 는 user_insights bullet 과 같은 문장(접두 "- " 없이).
   - 단발 관측이라도 모두 포함하세요. confidence는 시스템이 부여합니다.
   - 없으면 빈 배열 [].

4. "soul_updates": 에이전트의 성격·말투·호칭에 대한 사용자의 피드백
   - 사용자가 명시적으로 요청한 변경만 (예: "반말 써", "이모지 쓰지 마", "~라고 불러")
   - 기존 SOUL.md 내용과 중복이면 제외
   - 추측하지 말고, 사용자가 직접 지시한 것만 포함
   - 없으면 빈 문자열

5. "agent_updates": 에이전트 행동 규칙에 대한 사용자의 피드백
   - 사용자가 명시적으로 요청한 설정 변경만 (예: 캘린더 추가, 스킬 설정 등)
   - 기존 AGENT.md 내용과 중복이면 제외
   - 없으면 빈 문자열

6. "active_projects": 사용자가 현재(이번 대화 윈도우 안에서) 집중 중인 "프로젝트" 엔티티 리스트
   - 프로젝트는 사용자가 빌드/QA/리서치/운영 등 명확한 작업 단위를 가진 대상
     (예: "SimpleClaw", "Multica", "회사 발표 자료") — 단순 관심사·뉴스 토픽이 아님
   - 같은 프로젝트는 동일한 표기로 일관되게 출력 (대소문자/표기를 사이클마다 바꾸지 말 것)
   - 각 항목은 다음 필드를 포함:
     - "name": 프로젝트 이름 (사람이 읽는 표기, 예: "SimpleClaw")
     - "role": 사용자의 역할/관계를 한 줄로
       (예: "솔로 빌더 — 메모리 파이프라인 개선", "플랫폼 빌드/QA 운영자")
     - "recent_summary": 이번 윈도우의 최근 활동을 한두 문장으로
       (예: "BIZ-66 평가 후 sub-issue 10건을 분할하고 A·B 머지 리뷰 진행")
   - 윈도우 안에서 활동이 없는 프로젝트는 출력하지 마세요 (sidecar에 보관된
     기존 항목은 시스템이 자동으로 윈도우 외 처리합니다).
   - 없으면 빈 리스트 []

## 기존 SOUL.md 내용
{existing_soul_md}

## 기존 AGENT.md 내용
{existing_agent_md}

## 기존 USER.md 내용
{existing_user_md}

## 대화 내역
{conversations}

JSON 형식으로만 응답하세요:
{{"memory": "## {date}\\n- 항목1", "user_insights": "- 새 정보1", "user_insights_meta": [{{"topic": "주제키", "text": "새 정보1"}}], "soul_updates": "- 변경1", "agent_updates": "- 변경1", "active_projects": [{{"name": "...", "role": "...", "recent_summary": "..."}}]}}"""


# Phase 3 — 클러스터별 LLM 요약 프롬프트 (기존 + 신규 메시지를 받아 갱신된 라벨/요약 산출)
_CLUSTER_SUMMARY_PROMPT = """\
다음은 한 시맨틱 클러스터(주제 묶음)의 기존 요약과 새 메시지입니다.
기존 요약을 갱신하여 새 정보를 반영하되, 핵심 사실만 유지하고 중복은 제거하세요.
요약은 마크다운 bullet point로 작성합니다.

## 기존 라벨
{existing_label}

## 기존 요약
{existing_summary}

## 새 메시지(이번 드리밍 회차에 추가된 대화)
{new_messages}

JSON으로만 응답하세요:
{{"label": "10자 이내 짧은 한국어 라벨", "summary": "- 핵심 사실 1\\n- 핵심 사실 2\\n- ..."}}"""


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

    def create_backup(self, file_path: Path, max_backups: int = 3) -> Path | None:
        """파일 수정 전 타임스탬프가 포함된 .bak 백업을 생성한다.

        백업은 원본 파일의 부모 디렉토리 하위 memory-backup/ 폴더에 저장된다.
        최근 max_backups개만 유지하고 오래된 백업은 자동 삭제한다.

        Args:
            file_path: 백업할 원본 파일 경로.
            max_backups: 유지할 최대 백업 개수 (기본 3).

        Returns:
            생성된 백업 파일 경로. 원본 파일이 없으면 None.
        """
        if not file_path.is_file():
            return None

        backup_dir = file_path.parent / "memory-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_name = f"{file_path.stem}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        backup_path = backup_dir / backup_name
        shutil.copy2(file_path, backup_path)
        logger.info("Created backup: %s", backup_path)

        # 오래된 백업 정리: 같은 stem의 최근 max_backups개만 유지
        stem = file_path.stem
        existing_backups = sorted(
            backup_dir.glob(f"{stem}.*.bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_backup in existing_backups[max_backups:]:
            old_backup.unlink()
            logger.debug("Removed old backup: %s", old_backup)

        return backup_path

    def collect_unprocessed(self, last_dreaming: datetime | None = None) -> list:
        """마지막 드리밍 이후 미처리 대화 메시지를 수집한다.

        Args:
            last_dreaming: 마지막 드리밍 시각. None이면 최근 50개 메시지를 가져온다.

        Returns:
            처리 대상 ConversationMessage 리스트.

        BIZ-76: 자동 트리거(cron/recipe) 메시지는 ``auto_trigger_mode`` 에 따라
        제거 또는 stride sampling 으로 축소된다. 반환 순서는 시간순으로 보존된다.
        """
        if last_dreaming:
            raw = self._store.get_since(last_dreaming)
        else:
            raw = self._store.get_recent(limit=50)
        return self._apply_auto_trigger_filter(raw, key=lambda m: m)

    def collect_unprocessed_with_ids(
        self, last_dreaming: datetime | None = None
    ) -> list[tuple[int, ConversationMessage]]:
        """``collect_unprocessed`` 의 id-bearing 변형 (BIZ-77).

        인사이트 source 역추적을 위해 메시지 rowid 를 함께 수집해야 한다.
        반환 순서는 시간순 (id 오름차순) 으로 일관된다.

        BIZ-76: 자동 트리거 메시지는 ``auto_trigger_mode`` 에 따라 제거 또는
        축소된다. 제거된 메시지의 rowid 는 인사이트 source 에 포함되지 않으므로
        Admin "근거 보기" 가 자동 트리거를 가리키지 않는다 — 사용자에게 보이는
        인사이트와 그 근거가 모두 organic 발화로 일관된다.
        """
        if last_dreaming:
            raw = self._store.get_since_with_ids(last_dreaming)
        else:
            raw = self._store.get_recent_with_ids(limit=50)
        return self._apply_auto_trigger_filter(raw, key=lambda pair: pair[1])

    # ------------------------------------------------------------------
    # BIZ-76 — 자동 트리거 코퍼스 분리/축소
    # ------------------------------------------------------------------

    def _apply_auto_trigger_filter(self, items, key):
        """``auto_trigger_mode`` 에 따라 자동 트리거 메시지를 코퍼스에서 분리한다.

        ``items`` 는 ``ConversationMessage`` 리스트 또는
        ``(id, ConversationMessage)`` 튜플 리스트로 둘 다 처리한다 — 단일 진입점에서
        분류 정책을 일관되게 적용해 ``collect_unprocessed`` / ``..._with_ids``
        간 행동 차이가 생기지 않도록 한다.

        Args:
            items: 원본 코퍼스 (시간순).
            key: ``items`` 의 한 원소에서 ``ConversationMessage`` 를 꺼내는 함수.

        Returns:
            필터/샘플링이 적용된 새 리스트(시간순 보존).
        """
        if self._auto_trigger_mode == AUTO_TRIGGER_MODE_INCLUDE:
            # 가공 없이 그대로 — 레거시 호환 / 운영자 명시 옵트아웃.
            return list(items)

        organic = []
        auto = []
        for it in items:
            msg = key(it)
            if is_auto_trigger_channel(msg.channel):
                auto.append(it)
            else:
                organic.append(it)

        if self._auto_trigger_mode == AUTO_TRIGGER_MODE_EXCLUDE or not auto:
            # exclude — auto 전부 버린다. organic 만 시간순으로 그대로.
            # auto 가 비어 있을 때도 동일 경로(불필요한 sampling 계산 회피).
            return organic

        # downweight — stride sampling 으로 일정 비율만 보존.
        # weight=0 이면 결과적으로 exclude 와 동일하게 동작(0벡터 stride 회피).
        if self._auto_trigger_weight <= 0:
            return organic
        if self._auto_trigger_weight >= 1.0:
            # 가드 — 1.0 은 include 와 사실상 같으므로 모두 보존.
            return list(items)

        # stride 가 클수록 적게 남는다. weight=0.3 → stride=3 (1/3 보존).
        # round 가 0 을 만들 수 없도록 max(2, ...). 분수 나눗셈은 round 로 안정화.
        stride = max(2, int(round(1.0 / self._auto_trigger_weight)))
        sampled_auto = auto[::stride]

        # 시간순 보존을 위해 원본 인덱스로 재정렬. items 는 시간순 입력 가정.
        index_map = {id(it): i for i, it in enumerate(items)}
        combined = organic + sampled_auto
        combined.sort(key=lambda it: index_map[id(it)])
        return combined

    @property
    def insight_store(self) -> InsightStore | None:
        """인사이트 sidecar 저장소 (BIZ-73). Admin API 가 같은 sidecar 를 공유한다.

        ``insights_file`` 인자나 ``user_file`` 옆 자동 결정 경로가 둘 다 없으면
        ``None``. Admin API 라우팅은 None 일 때 503 으로 명시 disabled 응답.
        """
        return self._insights_store

    @property
    def suggestion_store(self) -> SuggestionStore | None:
        """Pending suggestion 큐 (BIZ-79). Admin API 가 같은 sidecar 를 공유.

        None 이면 dry-run 모드가 꺼져 있어 추출된 인사이트가 즉시 USER.md 에
        반영된다 — Admin API 의 ``/memory/suggestions/...`` 엔드포인트는 503 응답.
        """
        return self._suggestion_store

    @property
    def blocklist_store(self) -> BlocklistStore | None:
        """Reject 누적 블록리스트 (BIZ-79). Admin API reject 액션이 같은 store 에 add."""
        return self._blocklist_store

    @property
    def auto_promote_thresholds(self) -> tuple[float, int]:
        """``(confidence_floor, evidence_count_floor)`` 쌍 — 운영 가시성용."""
        return self._auto_promote_confidence, self._auto_promote_evidence_count

    async def summarize(self, messages: list) -> dict:
        """LLM을 사용하여 대화 요약을 생성한다.

        LLM 호출이 실패하거나 라우터가 없으면 단순 텍스트 요약으로 폴백한다.

        Args:
            messages: 요약 대상 대화 메시지 리스트.

        Returns:
            'memory'와 'user_insights' 키를 포함하는 딕셔너리.
        """
        if not messages:
            return {"memory": "", "user_insights": "", "user_insights_meta": [], "active_projects": []}

        if self._router:
            try:
                return await self._summarize_with_llm(messages)
            except Exception:
                logger.exception("LLM summarization failed, using fallback")

        return {
            "memory": self._summarize_fallback(messages),
            "user_insights": "",
            "user_insights_meta": [],
            "active_projects": [],
        }

    async def _summarize_with_llm(self, messages: list) -> dict:
        """LLM을 호출하여 대화를 분석하고 memory/user/soul/agent 업데이트를 추출한다."""
        from simpleclaw.llm.models import LLMRequest

        existing_user_md = ""
        if self._user_file and self._user_file.is_file():
            existing_user_md = self._user_file.read_text(encoding="utf-8")

        existing_soul_md = ""
        if self._soul_file and self._soul_file.is_file():
            existing_soul_md = self._soul_file.read_text(encoding="utf-8")

        existing_agent_md = ""
        if self._agent_file and self._agent_file.is_file():
            existing_agent_md = self._agent_file.read_text(encoding="utf-8")

        conv_lines = []
        for msg in messages:
            role = msg.role.value.upper()
            conv_lines.append(f"[{role}] {msg.content}")
        # LLM 컨텍스트 윈도우 초과를 방지하기 위해 8000자로 제한
        conversations = "\n".join(conv_lines)[:8000]

        date_str = datetime.now().strftime("%Y-%m-%d")
        prompt = _DREAMING_PROMPT.format(
            existing_soul_md=existing_soul_md or "(없음)",
            existing_agent_md=existing_agent_md or "(없음)",
            existing_user_md=existing_user_md or "(없음)",
            conversations=conversations,
            date=date_str,
        )

        request = LLMRequest(
            system_prompt="You are a conversation analyzer. Respond with valid JSON only.",
            user_message=prompt,
            backend_name=self._dreaming_model,
        )
        response = await self._router.send(request)
        return self._parse_llm_result(response.text.strip())

    def _parse_llm_result(self, raw: str) -> dict:
        """LLM의 JSON 응답을 파싱하여 memory/user/soul/agent 업데이트를 추출한다.

        LLM이 마크다운 코드 블록으로 감싼 경우에도 처리할 수 있다.
        JSON 파싱 실패 시 원본 텍스트 앞 500자를 memory로 사용한다.
        """
        # LLM이 ```json ... ``` 형태로 감싸는 경우 코드 블록 내용만 추출
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            result = json.loads(raw)
            # user_insights_meta — 객체 배열만 받아들이고, 형식에 안 맞는 항목은 silently drop.
            raw_meta = result.get("user_insights_meta") or []
            meta_items: list[dict] = []
            if isinstance(raw_meta, list):
                for item in raw_meta:
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("topic"), str)
                        and isinstance(item.get("text"), str)
                    ):
                        meta_items.append(
                            {"topic": item["topic"], "text": item["text"]}
                        )
            # BIZ-74: active_projects는 list[dict]로 들어와야 한다. LLM이 잘못된 타입
            # (문자열, dict 등)으로 반환하면 빈 리스트로 강등 — 본 단계에서 fail하면
            # 다른 dreaming 산출물(memory/insights)까지 같이 잃는다.
            raw_projects = result.get("active_projects") or []
            if not isinstance(raw_projects, list):
                logger.warning(
                    "active_projects field is not a list (got %s); ignoring",
                    type(raw_projects).__name__,
                )
                raw_projects = []
            return {
                "memory": result.get("memory", ""),
                "user_insights": result.get("user_insights", ""),
                "user_insights_meta": meta_items,
                "soul_updates": result.get("soul_updates", ""),
                "agent_updates": result.get("agent_updates", ""),
                "active_projects": raw_projects,
            }
        except json.JSONDecodeError:
            logger.warning("Failed to parse dreaming JSON: %s", raw[:200])
            return {
                "memory": raw[:500],
                "user_insights": "",
                "user_insights_meta": [],
                "soul_updates": "",
                "agent_updates": "",
                "active_projects": [],
            }

    def _summarize_fallback(self, messages: list) -> str:
        """LLM 없이 단순 텍스트 기반 요약을 생성한다. 각 메시지의 첫 5단어를 토픽으로 추출."""
        lines = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        lines.append(f"## {date_str}")
        lines.append("")

        topics = set()
        for msg in messages:
            words = msg.content.split()[:10]
            if words:
                topics.add(" ".join(words[:5]))

        for topic in list(topics)[:5]:
            lines.append(f"- {topic}...")

        return "\n".join(lines)

    def append_to_memory(self, summary: str) -> None:
        """드리밍 요약을 MEMORY.md의 managed:dreaming:journal 섹션에 append한다.

        BIZ-72: 마커 외부 영역은 보존된다. 마커가 없거나 잘못된 경우
        ``ProtectedSectionError``를 던져 호출자가 fail-closed로 응답하게 한다.
        """
        if not summary:
            return
        self._safe_append_in_section(
            self._memory_file, self._memory_section, summary
        )

    def _safe_append_in_section(
        self,
        file_path: Path,
        section_name: str,
        content: str,
    ) -> None:
        """파일의 ``managed:dreaming:<section_name>`` 안쪽에 ``content``를 append한다.

        Protected Section 모델의 1차 진입점. 마커 외부 바이트는 보존되고, 마커 자체도
        그대로 유지된다. 파일이 없거나 마커가 없으면 ``ProtectedSectionError``를 던지므로
        호출자(보통 ``run()``)가 잡아 fail-closed로 처리해야 한다.

        Args:
            file_path: 갱신 대상 파일.
            section_name: 갱신할 managed 섹션 이름.
            content: 섹션 내부에 append할 마크다운 본문.

        Raises:
            ProtectedSectionMissing: 파일이 없거나 해당 섹션이 정의돼 있지 않을 때.
            ProtectedSectionMalformed: 마커 자체가 잘못된 경우.
        """
        if not file_path.is_file():
            raise ProtectedSectionMissing(
                f"managed 파일이 존재하지 않음: {file_path} (section={section_name})"
            )
        existing = file_path.read_text(encoding="utf-8")
        new_text = append_to_section(existing, section_name, content)
        # ``append_to_section``은 변경이 없으면 입력을 그대로 반환 — 불필요한 mtime 변경 방지
        if new_text != existing:
            file_path.write_text(new_text, encoding="utf-8")
            logger.info(
                "Updated managed section '%s' in %s", section_name, file_path
            )

    def _format_dated_block(self, header: str, content: str) -> str:
        """``## {header} ({date})`` 헤더를 붙인 dated block을 생성한다.

        managed 섹션 내부에 일자별 dreaming 결과를 append할 때의 표준 포맷.
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        return f"## {header} ({date_str})\n{content.strip()}"

    def update_user_file(self, insights: str) -> None:
        """새로운 사용자 인사이트를 USER.md의 managed:dreaming:insights 섹션에 추가한다."""
        if not self._user_file or not insights:
            return
        block = self._format_dated_block("Dreaming Insights", insights)
        self._safe_append_in_section(self._user_file, self._user_section, block)

    # ------------------------------------------------------------------
    # BIZ-73 — 인사이트 메타 (sidecar JSONL) 갱신
    # ------------------------------------------------------------------

    @property
    def reject_blocklist(self) -> RejectBlocklistStore | None:
        """BIZ-78 — reject 차단 리스트 저장소. ``None`` 이면 비활성.

        Admin Review Loop(H, BIZ-79) 가 같은 store 를 공유해 사용자 거부 신호를
        등록한다.
        """
        return self._reject_store

    def apply_insight_meta(
        self,
        meta_items: list[dict],
        source_msg_ids: list[int] | None = None,
    ) -> tuple[list[InsightMeta], list[InsightMeta]]:
        """이번 회차의 인사이트 메타를 sidecar 와 병합·저장한다.

        BIZ-79 dry-run 모드 (``suggestion_store`` 가 주입된 경우):
        1. 블록리스트에 등록된 토픽 관측은 merge 전에 필터링한다 — 같은 인사이트가
           재추출되는 것을 차단한다(거부 → 차단 루프의 한 끝).
        2. 병합 후, 변경된 각 인사이트를 두 갈래로 라우팅:
           - 자동 적용(auto-promote): ``confidence`` 와 ``evidence_count`` 가
             동시에 임계치를 충족하면 ``promoted`` 로 반환 — 호출자가 USER.md 에
             즉시 append.
           - 큐 적재: 그 외 모든 변경은 pending suggestion 으로 큐에 들어간다.
             기존 pending 행이 있으면 in-place 갱신 (한 토픽당 1행 보장).

        레거시 모드 (``suggestion_store`` 미주입):
        ``promoted`` 는 ``is_promoted`` 기준 — 호출자(``run``)가 별도 로직 없이
        그대로 동작한다.

        Args:
            meta_items: ``[{"topic": ..., "text": ...}, ...]`` 형태의 LLM 추출물.
            source_msg_ids: 이번 회차에 분석한 메시지 rowid 목록 (BIZ-77 source linkage).
                None 이면 빈 리스트로 처리. 신규/강화된 모든 인사이트에 동일 부착.

        Returns:
            (changed, promoted)
            - changed: 이번 회차에 추가/갱신된 인사이트 (블록리스트로 필터된 것 제외).
            - promoted: 자동 적용 대상 — 호출자가 USER.md 본문에 반영해야 하는 항목.
            sidecar 저장소가 비활성이거나 입력이 비어있으면 (빈 리스트, 빈 리스트).
        """
        if not self._insights_store or not meta_items:
            return [], []

        now = datetime.now()
        ids = list(source_msg_ids or [])
        # BIZ-78 — reject 차단 리스트에 있는 topic 은 추출 자체를 무효화한다.
        # load() 는 만료된 항목을 자동 제외하므로 여기서 추가 필터 없이 사용해도 OK.
        blocklist = (
            self._reject_store.load(now=now) if self._reject_store else {}
        )
        blocked_topics_seen: list[str] = []
        observations: list[InsightMeta] = []
        # BIZ-79 — blocklist 사전 필터. 이미 reject 된 topic 은 sidecar 에 진입하지 않는다.
        # blocklist 가 비활성(None) 이면 모든 관측을 통과시킨다.
        for item in meta_items:
            topic = (item.get("topic") or "").strip()
            text = (item.get("text") or "").strip()
            if not topic or not text:
                continue
            # BIZ-78: reject_store 기반 차단 (legacy TTL 기반)
            if blocklist and normalize_topic(topic) in blocklist:
                blocked_topics_seen.append(topic)
                continue
            # BIZ-79: 블록리스트 토픽은 merge 이전에 drop — 같은 인사이트 재추출 차단.
            # ``BlocklistStore`` 가 주입되지 않은 경우(레거시) 차단 없이 통과.
            if self._blocklist_store is not None and self._blocklist_store.is_blocked(
                topic
            ):
                logger.info(
                    "Skipping blocklisted insight topic: %r",
                    normalize_topic(topic),
                )
                continue
            observations.append(
                InsightMeta(
                    topic=topic,
                    text=text,
                    evidence_count=1,
                    confidence=0.0,  # merge_insights 가 재계산
                    first_seen=now,
                    last_seen=now,
                    source_msg_ids=list(ids),
                )
            )

        if blocked_topics_seen:
            logger.info(
                "Dropped %d blocklisted topic observation(s): %s",
                len(blocked_topics_seen),
                blocked_topics_seen,
            )

        if not observations:
            return [], []

        existing = self._insights_store.load()
        merged, changed = merge_insights(
            existing, observations, self._insight_promotion_threshold
        )
        self._insights_store.save_all(merged)

        # BIZ-79: 변경 항목을 자동 적용 vs 큐 적재로 라우팅.
        promoted: list[InsightMeta] = []
        if self._suggestion_store is not None:
            queued = 0
            for meta in changed:
                if self._meets_auto_promote(meta):
                    promoted.append(meta)
                    # auto-promote 가 발동된 토픽이 이전 사이클에서 큐에 남아 있었다면
                    # 자동으로 accepted 처리한다 — USER.md 에 이미 들어갈 내용을
                    # 운영자가 다시 보지 않게 정리.
                    existing = self._suggestion_store.find_pending_by_topic(
                        meta.topic
                    )
                    if existing is not None:
                        self._suggestion_store.update_status(
                            existing.id, "accepted"
                        )
                    continue
                # pending 큐에 적재 (한 토픽당 1행 보장 — 같은 토픽 반복 강화는
                # in-place 갱신).
                self._suggestion_store.upsert_pending(meta)
                queued += 1
            logger.info(
                "Insights updated: %d changed, %d auto-applied, %d queued "
                "(promote_conf>=%.2f & ev>=%d, sidecar_threshold=%d)",
                len(changed), len(promoted), queued,
                self._auto_promote_confidence,
                self._auto_promote_evidence_count,
                self._insight_promotion_threshold,
            )
        else:
            # 레거시 모드: ``is_promoted`` 기준 (BIZ-73 호환).
            promoted = [
                m for m in changed
                if is_promoted(m, self._insight_promotion_threshold)
            ]
            logger.info(
                "Insights updated: %d changed, %d promoted (threshold=%d)",
                len(changed), len(promoted), self._insight_promotion_threshold,
            )
        return changed, promoted

    def _meets_auto_promote(self, meta: InsightMeta) -> bool:
        """자동 적용 조건 — confidence/evidence_count 가 **동시에** 임계치 이상.

        한쪽만 만족하는 경우(예: 단발 고신뢰)는 큐로 보내 운영자 검수에 맡긴다 —
        BIZ-79 DoD §1 "confidence ≥ X AND evidence_count ≥ Y simultaneously".
        """
        return (
            meta.confidence >= self._auto_promote_confidence
            and meta.evidence_count >= self._auto_promote_evidence_count
        )

    @staticmethod
    def _format_auto_applied_bullets(items: list[InsightMeta]) -> str:
        """자동 적용 인사이트들의 ``text`` 를 USER.md 용 bullet 텍스트로 합친다.

        각 ``text`` 는 보통 이미 "- " 접두 없이 한 줄. 빈 줄/중복은 제거하여
        깔끔한 bullet 블록을 만든다. 빈 입력이면 빈 문자열 반환.
        """
        seen: set[str] = set()
        bullets: list[str] = []
        for meta in items:
            text = (meta.text or "").strip()
            if not text:
                continue
            # 이미 사용자가 적은 prefix 가 있는 경우 그대로 유지.
            line = text if text.startswith(("-", "*")) else f"- {text}"
            if line in seen:
                continue
            seen.add(line)
            bullets.append(line)
        return "\n".join(bullets)

    def append_insight_to_user_file(self, text: str) -> None:
        """단일 인사이트(또는 사용자 편집본)를 USER.md insights 섹션에 append.

        Admin API 의 accept / edit 핸들러가 사용한다. 빈 텍스트는 무시.
        """
        text = (text or "").strip()
        if not text or not self._user_file:
            return
        line = text if text.startswith(("-", "*")) else f"- {text}"
        block = self._format_dated_block("Dreaming Insights", line)
        self._safe_append_in_section(self._user_file, self._user_section, block)

    # ------------------------------------------------------------------
    # BIZ-78 — Decay & Re-evaluation
    # ------------------------------------------------------------------

    def apply_decay(
        self, now: datetime | None = None
    ) -> list[InsightMeta]:
        """``last_seen`` 기준 N일 이상 reinforcement 가 없으면 archive 처리.

        DoD #1 — 30일(기본, ``decay_archive_after_days`` config) 이상 재관측되지
        않은 인사이트는 sidecar 의 ``archived_at`` 이 세팅되고 USER.md 의
        ``managed:dreaming:archive`` 섹션에 dated 흔적이 추가된다(섹션이 없으면
        sidecar 만 갱신).

        반환값은 *이번 호출에서 새로 archive 된* 인사이트 리스트 — 이미 archive 된
        항목은 건드리지 않으므로 결과에 포함되지 않는다.

        Args:
            now: 비교 기준 시각. 기본 현재 시각. 테스트에서 30일 경과를 시뮬레이트할 때
                의도적으로 오버라이드한다.

        Returns:
            새로 archive 된 ``InsightMeta`` 리스트. decay 비활성/비대상이면 빈 리스트.
        """
        if (
            self._insights_store is None
            or self._decay_archive_after_days is None
        ):
            return []

        n = now or datetime.now()
        cutoff = n - timedelta(days=self._decay_archive_after_days)
        loaded = self._insights_store.load()
        if not loaded:
            return []

        newly_archived: list[InsightMeta] = []
        for meta in loaded.values():
            # 이미 archive 되었거나 cutoff 이후 reinforcement 가 있으면 패스.
            if meta.is_archived():
                continue
            if meta.last_seen >= cutoff:
                continue
            meta.archived_at = n
            newly_archived.append(meta)

        if not newly_archived:
            return []

        # sidecar 영속화 — archived_at 만 바뀌고 행 자체는 보존(부활 가능하도록).
        self._insights_store.save_all(loaded)

        # USER.md archive 섹션이 있으면 dated 흔적 추가. 없으면 sidecar 만 갱신하고 끝.
        # (기존 USER.md 가 archive 섹션을 안 갖고 있어도 깨지지 않게 — 호환성 우선)
        if self._user_file and self._user_file.is_file():
            try:
                user_text = self._user_file.read_text(encoding="utf-8")
                if has_managed_section(user_text, self._archive_section):
                    block_lines = [
                        f"- [{m.topic}] {m.text} (last_seen="
                        f"{m.last_seen.strftime('%Y-%m-%d')})"
                        for m in newly_archived
                    ]
                    block = self._format_dated_block(
                        "Archived (decay)", "\n".join(block_lines)
                    )
                    self._safe_append_in_section(
                        self._user_file, self._archive_section, block
                    )
                else:
                    logger.info(
                        "Archive section '%s' missing in %s — sidecar only updated. "
                        "Add markers to surface archived insights in USER.md.",
                        self._archive_section, self._user_file,
                    )
            except ProtectedSectionError as exc:
                # archive 섹션 마커가 손상된 경우 — sidecar 갱신은 이미 끝났고, 이 흔적
                # 기록만 실패한다. 다음 사이클의 preflight 가 동일 문제를 다시 잡거나
                # 운영자가 수정할 때까지 markdown 흔적은 누락된다(데이터 손실은 없다).
                logger.warning(
                    "Failed to write archive markers in %s: %s. Sidecar archive_at "
                    "is set; markdown trail is missing until markers are repaired.",
                    self._user_file, exc,
                )

        logger.info(
            "Decay archived %d insight(s) (cutoff=%s, days=%d)",
            len(newly_archived),
            cutoff.isoformat(),
            self._decay_archive_after_days,
        )
        return newly_archived

    def register_reject(
        self,
        topic: str,
        scope: str = "global",
        ttl_days: int | None = -1,
        reason: str = "",
        now: datetime | None = None,
    ) -> bool:
        """사용자 reject 신호를 처리한다 — DoD #2.

        1. 인사이트 sidecar 에서 해당 topic 행을 즉시 삭제(archive 가 아니라 *폐기*).
        2. reject 차단 리스트(``rejects.jsonl``)에 등록 — 다음 회차부터 같은 topic 의
           추출이 ``apply_insight_meta`` 단계에서 drop 된다.

        ``ttl_days`` 의미:
            - ``-1`` (기본 sentinel): 생성자에 전달된 ``reject_default_ttl_days`` 사용.
              해당 기본값이 None 이면 영구 차단.
            - ``None``: 영구 차단(가장 흔한 케이스).
            - 양수: 그 일수 이후 자동 해제.

        Args:
            topic: 거부할 인사이트의 topic(원문/정규형 모두 가능).
            scope: 차단 범위. 기본 ``global``.
            ttl_days: 차단 지속 기간(일). 위 의미 참조.
            reason: 자유 텍스트 사유 — Admin UI 에 표시되며 운영자 검수에 활용.
            now: 시각 오버라이드(테스트용).

        Returns:
            차단 리스트에 새 항목이 등록·갱신됐으면 True. 차단 리스트가 비활성이거나
            topic 이 비어있으면 False.
        """
        if self._reject_store is None:
            logger.warning(
                "register_reject called but reject blocklist is disabled "
                "(insights sidecar not configured)"
            )
            return False
        if not topic or not topic.strip():
            return False

        # ttl_days sentinel 해석.
        if ttl_days == -1:
            effective_ttl_days = self._reject_default_ttl_days
        else:
            effective_ttl_days = ttl_days
        if effective_ttl_days is None:
            ttl_seconds: int | None = None
        else:
            try:
                ttl_seconds = int(effective_ttl_days) * 86400
                if ttl_seconds <= 0:
                    ttl_seconds = None
            except (TypeError, ValueError):
                ttl_seconds = None

        # 1) sidecar 에서 즉시 삭제 — archive 와 다른 점은 *완전 폐기* 라는 것.
        #    부활 메커니즘은 reject 에서 작동하지 않는다(차단 리스트가 재추출을 막음).
        if self._insights_store is not None:
            insights = self._insights_store.load()
            key = normalize_topic(topic)
            if key and key in insights:
                insights.pop(key, None)
                self._insights_store.save_all(insights)
                logger.info("Reject: removed insight %r from sidecar", topic)

        # 2) 차단 리스트에 등록.
        try:
            self._reject_store.add(
                topic=topic.strip(),
                scope=scope,
                ttl_seconds=ttl_seconds,
                reason=reason,
                now=now,
            )
        except ValueError:
            return False
        logger.info(
            "Reject: blocked topic %r (ttl_seconds=%s, scope=%s)",
            topic.strip(), ttl_seconds, scope,
        )
        return True

    def update_soul_file(self, updates: str) -> None:
        """에이전트 성격/말투 변경을 SOUL.md의 managed:dreaming:dreaming-updates에 추가한다."""
        if not self._soul_file or not updates:
            return
        block = self._format_dated_block("Dreaming Updates", updates)
        self._safe_append_in_section(self._soul_file, self._soul_section, block)

    def update_agent_file(self, updates: str) -> None:
        """에이전트 행동 규칙 변경을 AGENT.md의 managed:dreaming:dreaming-updates에 추가한다."""
        if not self._agent_file or not updates:
            return
        block = self._format_dated_block("Dreaming Updates", updates)
        self._safe_append_in_section(self._agent_file, self._agent_section, block)

    # ------------------------------------------------------------------
    # BIZ-74 — Active Projects (USER.md managed:dreaming:active-projects)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Phase 3 — 클러스터 기반 그래프형 드리밍
    # ------------------------------------------------------------------

    def assign_clusters_for_unprocessed(self) -> dict[int, list[ConversationMessage]]:
        """클러스터링되지 않은 메시지를 점진 할당하고 영향받은 클러스터별 멤버를 반환한다.

        과정:
        1. ``get_unclustered_with_embeddings()``로 후보 메시지를 얻는다.
        2. 각 메시지에 대해 ``IncrementalClusterer.find_nearest()`` 실행:
           - 임계값 이상이면 기존 클러스터에 부착(centroid·member_count incremental update).
           - 미만이면 신규 클러스터 생성(첫 멤버의 임베딩이 곧 centroid).
        3. ``messages.cluster_id``를 갱신하고, 영향받은 클러스터별로 그 회차에 새로 들어온 메시지 목록을 모은다.

        Returns:
            ``{cluster_id: [ConversationMessage, ...]}`` — 이번 회차에 갱신된 클러스터와 멤버.
            클러스터링이 비활성이거나 처리 대상이 없으면 빈 딕셔너리.
        """
        if not self._enable_clusters or self._clusterer is None:
            return {}

        unprocessed = self._store.get_unclustered_with_embeddings()
        if not unprocessed:
            return {}

        # 매 메시지마다 list_clusters를 다시 호출하지 않고 인메모리 캐시를 갱신한다.
        # 신규 클러스터를 만들면 캐시에도 추가하여 같은 회차의 후속 메시지가 그 클러스터에 부착될 수 있게 한다.
        clusters_cache: dict[int, ClusterRecord] = {
            c.id: c for c in self._store.list_clusters()
        }
        affected: dict[int, list[ConversationMessage]] = {}

        for mid, msg, embedding in unprocessed:
            try:
                assignment = self._clusterer.find_nearest(
                    embedding, list(clusters_cache.values())
                )
            except ValueError as exc:
                # 0벡터 등 의미 없는 임베딩 — 스킵
                logger.warning("Skipping message %d: %s", mid, exc)
                continue

            if assignment.cluster_id is not None:
                # 기존 클러스터에 부착 — centroid는 누적 평균으로, member_count는 +1
                cluster = clusters_cache[assignment.cluster_id]
                new_centroid = self._clusterer.update_centroid(
                    cluster.centroid, cluster.member_count, embedding
                )
                new_count = cluster.member_count + 1
                self._store.update_cluster(
                    cluster.id,
                    centroid=new_centroid,
                    member_count=new_count,
                )
                # 캐시 동기화 — 같은 회차 후속 메시지가 본 centroid 기준으로 비교되도록
                clusters_cache[cluster.id] = ClusterRecord(
                    id=cluster.id,
                    label=cluster.label,
                    centroid=new_centroid,
                    summary=cluster.summary,
                    member_count=new_count,
                    updated_at=datetime.now(),
                )
                cid = cluster.id
            else:
                # 신규 클러스터 — 첫 멤버 임베딩을 centroid로
                cid = self._store.create_cluster(
                    label="",  # 라벨은 LLM 요약 단계에서 채움
                    centroid=embedding,
                    summary="",
                    member_count=1,
                )
                clusters_cache[cid] = ClusterRecord(
                    id=cid,
                    label="",
                    centroid=embedding.copy(),
                    summary="",
                    member_count=1,
                    updated_at=datetime.now(),
                )

            self._store.assign_cluster(mid, cid)
            affected.setdefault(cid, []).append(msg)

        return affected

    async def summarize_cluster(
        self,
        messages: list[ConversationMessage],
        existing_label: str = "",
        existing_summary: str = "",
    ) -> dict[str, str]:
        """단일 클러스터의 신규 메시지를 받아 갱신된 라벨·요약을 반환한다.

        LLM 라우터가 없거나 호출이 실패하면 단순 폴백을 사용한다.

        Args:
            messages: 이번 회차에 이 클러스터에 부착된 메시지들.
            existing_label: 기존 라벨 (없으면 빈 문자열).
            existing_summary: 기존 요약 (없으면 빈 문자열).

        Returns:
            ``{"label": str, "summary": str}`` — 갱신된 라벨과 요약 본문.
        """
        if not messages:
            return {"label": existing_label, "summary": existing_summary}

        if self._router:
            try:
                return await self._summarize_cluster_with_llm(
                    messages, existing_label, existing_summary
                )
            except Exception:
                logger.exception("LLM cluster summarization failed, using fallback")

        return self._summarize_cluster_fallback(
            messages, existing_label, existing_summary
        )

    async def _summarize_cluster_with_llm(
        self,
        messages: list[ConversationMessage],
        existing_label: str,
        existing_summary: str,
    ) -> dict[str, str]:
        """LLM에게 클러스터 메시지를 분석시켜 갱신된 라벨/요약을 받는다."""
        from simpleclaw.llm.models import LLMRequest

        conv_lines = []
        for msg in messages:
            role = msg.role.value.upper()
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
            conv_lines.append(f"[{ts} {role}] {msg.content}")
        new_block = "\n".join(conv_lines)[:6000]

        prompt = _CLUSTER_SUMMARY_PROMPT.format(
            existing_label=existing_label or "(없음)",
            existing_summary=existing_summary or "(없음)",
            new_messages=new_block,
        )
        request = LLMRequest(
            system_prompt=(
                "You are a memory clustering assistant. "
                "Respond with valid JSON only."
            ),
            user_message=prompt,
            backend_name=self._dreaming_model,
        )
        response = await self._router.send(request)
        return self._parse_cluster_result(
            response.text.strip(), existing_label, existing_summary
        )

    def _parse_cluster_result(
        self,
        raw: str,
        existing_label: str,
        existing_summary: str,
    ) -> dict[str, str]:
        """LLM 응답 JSON에서 label/summary를 추출한다.

        파싱 실패 시 기존 값을 유지하고 raw 텍스트 앞 200자를 summary로 폴백한다.
        """
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            data = json.loads(raw)
            label = (data.get("label") or existing_label or "").strip()
            summary = (data.get("summary") or existing_summary or "").strip()
            return {"label": label, "summary": summary}
        except json.JSONDecodeError:
            logger.warning("Failed to parse cluster JSON: %s", raw[:200])
            return {
                "label": existing_label,
                "summary": existing_summary or raw[:200],
            }

    def _summarize_cluster_fallback(
        self,
        messages: list[ConversationMessage],
        existing_label: str,
        existing_summary: str,
    ) -> dict[str, str]:
        """LLM 없이 단순 텍스트 기반 클러스터 요약(메시지 첫 줄을 bullet로 나열).

        라벨은 기존값을 우선하며, 비어있으면 첫 메시지의 앞 8글자를 사용한다.
        """
        if existing_label:
            label = existing_label
        else:
            first_text = messages[0].content if messages else ""
            label = first_text[:8].strip() or "untagged"

        bullet_lines: list[str] = []
        if existing_summary.strip():
            bullet_lines.append(existing_summary.strip())
        for msg in messages:
            snippet = msg.content.replace("\n", " ").strip()[:80]
            if snippet:
                bullet_lines.append(f"- {snippet}")
        summary = "\n".join(bullet_lines)
        return {"label": label, "summary": summary}

    def upsert_memory_section(
        self, cluster_id: int, label: str, summary: str
    ) -> None:
        """MEMORY.md의 ``<!-- cluster:N -->`` 섹션을 갱신하거나 신규 추가한다.

        BIZ-72: cluster 섹션은 ``<!-- managed:dreaming:clusters -->`` 컨테이너
        안쪽에서만 살아있다. 컨테이너가 없거나 잘못된 경우 ``ProtectedSectionError``를
        던져 호출자가 fail-closed 처리하게 한다. 컨테이너 외부의 사용자 콘텐츠
        (예: 정체성 메모, 수기 메모)는 절대 변경되지 않는다.

        규칙:
        - 컨테이너 내부에서 동일 ``cluster_id`` 마커가 있으면 본문만 교체.
        - 컨테이너 내부에 마커가 없으면 컨테이너 끝부분에 새 섹션 append.
        """
        if not self._memory_file.is_file():
            raise ProtectedSectionMissing(
                f"managed 파일이 존재하지 않음: {self._memory_file} "
                f"(section={self._cluster_section})"
            )

        existing = self._memory_file.read_text(encoding="utf-8")
        # 컨테이너 본문(즉, dreaming이 자유롭게 cluster 마커를 두를 수 있는 영역)을 읽어온다.
        container_body = get_section_body(existing, self._cluster_section)
        # 끝부분 빈 줄을 정규화 — 항상 단일 trailing newline 기준으로 작업해 새 섹션 append시
        # 인접 빈 줄이 끝없이 늘어나는 것을 방지.
        normalized_body = container_body.strip("\n")

        section_body = self._format_cluster_section_body(cluster_id, label, summary)
        start_marker = _CLUSTER_MARKER_START.format(cid=cluster_id)
        end_marker = _CLUSTER_MARKER_END.format(cid=cluster_id)
        new_block = f"{start_marker}\n{section_body}\n{end_marker}"

        section_re = re.compile(
            rf"{re.escape(start_marker)}\n?.*?\n?{re.escape(end_marker)}",
            re.DOTALL,
        )
        if section_re.search(normalized_body):
            updated_body = section_re.sub(new_block, normalized_body, count=1)
        else:
            # 신규 cluster — 컨테이너 끝에 빈 줄 한 칸 띄우고 append
            if normalized_body:
                updated_body = normalized_body + "\n\n" + new_block
            else:
                updated_body = new_block

        new_text = replace_section_body(
            existing, self._cluster_section, updated_body
        )
        if new_text != existing:
            self._memory_file.write_text(new_text, encoding="utf-8")
            logger.info(
                "Upserted cluster %d in memory file (managed section '%s')",
                cluster_id,
                self._cluster_section,
            )

    @staticmethod
    def _format_cluster_section_body(
        cluster_id: int, label: str, summary: str
    ) -> str:
        """클러스터 섹션 본문을 사람이 읽기 좋은 마크다운으로 포맷한다."""
        header_label = label.strip() or f"cluster {cluster_id}"
        body = summary.strip() or "(no summary yet)"
        return f"## {header_label} (cluster {cluster_id})\n\n{body}"

    def _preflight_protected_sections(self) -> None:
        """쓰기 시작 전에 모든 대상 파일이 필요한 managed 섹션을 갖췄는지 검증.

        BIZ-72: "Fail-closed" 보장의 핵심 — 한 파일이라도 마커가 없거나 잘못돼 있으면
        쓰기 자체를 시작하지 않는다. 부분 변경(한 파일만 변경되고 다른 파일은 abort)
        같은 어정쩡한 상태가 절대 만들어지지 않게 한다.

        검증되는 섹션:
        - MEMORY.md: ``memory_section``(레거시 append) 또는 ``cluster_section``
          (Phase 3) — ``enable_clusters`` 여부에 따라 다름.
        - USER.md: ``user_section`` (파일이 설정돼 있을 때).
        - SOUL.md: ``soul_section`` (파일이 설정돼 있을 때).
        - AGENT.md: ``agent_section`` (파일이 설정돼 있을 때).

        Raises:
            ProtectedSectionError: 어느 한 파일이라도 검증 실패 시.
        """
        targets: list[tuple[Path, str]] = []
        memory_section_name = (
            self._cluster_section if self._enable_clusters else self._memory_section
        )
        targets.append((self._memory_file, memory_section_name))
        if self._user_file:
            targets.append((self._user_file, self._user_section))
            # BIZ-74: active-projects 가 활성화된 경우 같은 USER.md 안의
            # ``active-projects`` 섹션도 사전 검증한다. 누락 시 전체 사이클 abort.
            if self.is_active_projects_enabled():
                targets.append((self._user_file, self._active_projects_section))
        if self._soul_file:
            targets.append((self._soul_file, self._soul_section))
        if self._agent_file:
            targets.append((self._agent_file, self._agent_section))

        for file_path, section_name in targets:
            if not file_path.is_file():
                raise ProtectedSectionMissing(
                    f"Dreaming preflight 실패: {file_path}가 존재하지 않음 "
                    f"(필요 섹션: {section_name}). 먼저 managed 마커가 포함된 템플릿을 "
                    f"수동 또는 ``protected_section.ensure_initialized``로 생성하세요."
                )
            text = file_path.read_text(encoding="utf-8")
            # ``get_section_body``는 섹션이 없으면 ProtectedSectionMissing,
            # 마커가 잘못됐으면 ProtectedSectionMalformed를 던진다.
            get_section_body(text, section_name)

    async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
        """전체 드리밍 파이프라인을 실행한다.

        1. 미처리 대화 메시지를 수집한다.
        2. 처리할 내용이 있으면 (a) Protected Section 사전 검증 후 (b) 대상 파일들을 백업한다.
        3. LLM을 통해 요약을 생성한다 (USER/SOUL/AGENT 갱신용).
        4. ``_enable_clusters=True``면 그래프형 드리밍을 추가로 실행하여
           MEMORY.md를 시간순 append 대신 클러스터별 마커 섹션 upsert로 갱신한다.
           False(기본값)면 기존 append 동작을 유지한다.
        5. USER/SOUL/AGENT 갱신은 두 모드 모두 동일하게 수행한다.

        BIZ-72 Fail-closed 시맨틱:
        - 사전 검증 단계(2-a)에서 한 파일이라도 managed 섹션이 누락/오염돼 있으면
          전체 사이클을 즉시 abort하고 ``None``을 반환한다. 어떤 파일도 변경되지 않는다.
        - 쓰기 도중에 ``ProtectedSectionError``가 던져지면(예: 파일이 외부에서 동시
          편집됨) 백업에서 모든 대상 파일을 복원하고 ``None``을 반환한다.

        Args:
            last_dreaming: 마지막 드리밍 시각. None이면 최근 메시지를 대상으로 한다.

        Returns:
            생성된 MemoryEntry 객체. 처리할 메시지가 없거나, fail-closed로 abort됐거나,
            결과가 비어있으면 None.
        """
        # BIZ-77 — 메시지를 id 와 함께 수집한다. 분석 자체는 message 객체만 쓰지만
        # 인사이트 source 역추적을 위해 rowid 를 sidecar 에 기록해야 하기 때문이다.
        id_pairs = self.collect_unprocessed_with_ids(last_dreaming)
        if not id_pairs:
            logger.info("No new messages to process for dreaming.")
            return None

        source_msg_ids = [mid for mid, _ in id_pairs]
        messages = [msg for _, msg in id_pairs]

        # BIZ-72: 쓰기 시작 전 Protected Section 사전 검증. 실패 시 어떤 파일도
        # 백업조차 만들지 않고 즉시 종료(불필요한 디스크 I/O 방지).
        try:
            self._preflight_protected_sections()
        except ProtectedSectionError as exc:
            logger.error(
                "Dreaming aborted (preflight): %s. 파일은 변경되지 않았습니다.",
                exc,
            )
            return None

        # 처리할 메시지가 있고 사전 검증 통과 — 백업 생성 후 본격 작업
        backups: list[tuple[Path, Path | None]] = []
        backups.append((self._memory_file, self.create_backup(self._memory_file)))
        if self._user_file:
            backups.append((self._user_file, self.create_backup(self._user_file)))
        if self._soul_file:
            backups.append((self._soul_file, self.create_backup(self._soul_file)))
        if self._agent_file:
            backups.append((self._agent_file, self.create_backup(self._agent_file)))

        result = await self.summarize(messages)
        memory_summary = result.get("memory", "")
        user_insights = result.get("user_insights", "")
        user_insights_meta = result.get("user_insights_meta", []) or []
        soul_updates = result.get("soul_updates", "")
        agent_updates = result.get("agent_updates", "")
        # BIZ-74: 관측치는 빈 리스트일 수 있다(LLM이 식별 못함). 빈 리스트여도
        # update_active_projects를 호출해 윈도우 외 항목이 USER.md에서 사라지도록 한다.
        active_project_obs = result.get("active_projects", []) or []

        # BIZ-78: decay 적용을 *meta 갱신 이전* 에 수행한다. 같은 회차에 reinforcement
        # 가 들어오는 topic 은 archive 가 됐다가 즉시 부활하는 비효율을 피하기 위해 —
        # 아니, 사실 그 *순서가 의도된* 것이기도 하다: archive 흔적이 남고 그 회차에
        # 부활이 명시적으로 기록되어 운영자에게 "한 번 archive 됐다가 부활됨" 을 보여
        # 준다. 결정 근거: archive 와 reinforcement 가 같은 회차에 동시 발생하는 건
        # 매우 드물고, 두 경로가 모두 sidecar 에서 보이는 게 진단에 유리.
        if self._insights_store is not None:
            try:
                self.apply_decay()
            except Exception:
                # decay 실패는 사이클 자체를 중단시키지 않는다 — 다음 회차에 다시 시도.
                logger.exception("apply_decay failed; continuing dreaming cycle")

        # BIZ-73 + BIZ-77: 인사이트 메타 sidecar 갱신 — USER.md 본문 append 보다 먼저
        # 실행하여 어떤 항목이 "승격" 됐는지(USER.md에 high-confidence 표시 가능)
        # 사전 판단할 수 있게 한다. BIZ-77 부터는 이번 회차에 분석된 모든 메시지의
        # rowid 를 신규/강화된 모든 인사이트에 부착한다 — Admin "근거 보기" 의 입력.
        # 주의: sidecar 갱신은 try 바깥에서 수행하지만, fail-closed 의미를 깨지 않기 위해
        # 어떤 markdown 파일도 아직 변경되지 않았다(preflight 통과 직후). sidecar 자체는
        # JSONL atomic-rename 으로 항상 일관된 상태가 보장된다.
        promoted_meta: list[InsightMeta] = []
        if user_insights_meta and self._insights_store:
            _, promoted_meta = self.apply_insight_meta(
                user_insights_meta, source_msg_ids=source_msg_ids
            )

        cluster_summary_text = ""
        active_projects_rendered: list[ActiveProject] = []
        try:
            # USER/SOUL/AGENT는 두 모드 공통으로 갱신.
            # BIZ-79: dry-run 모드(suggestion_store 활성)에서는 LLM 의 user_insights
            # 블록을 통째로 USER.md 에 쓰지 않는다 — 큐를 우회하면 review 의미가
            # 사라지기 때문이다. 대신 ``apply_insight_meta`` 가 자동 적용 대상으로
            # 분류한 ``promoted_meta`` 만 한 줄씩 bullet 으로 append 한다.
            # 레거시 모드(suggestion_store 미주입)에서는 기존 동작 유지.
            if self._suggestion_store is not None:
                auto_text = self._format_auto_applied_bullets(promoted_meta)
                if auto_text:
                    self.update_user_file(auto_text)
            elif user_insights:
                self.update_user_file(user_insights)
            if soul_updates:
                self.update_soul_file(soul_updates)
            if agent_updates:
                self.update_agent_file(agent_updates)

            # BIZ-74: USER.md active-projects 섹션 in-place 갱신.
            # 활성화돼 있을 때만 호출되며, 빈 관측이어도 실행해 윈도우 외 항목이
            # 자연스럽게 섹션에서 사라지도록 한다.
            if self.is_active_projects_enabled():
                active_projects_rendered = self.update_active_projects(
                    active_project_obs
                )

            # MEMORY.md 갱신은 클러스터 모드 여부에 따라 분기
            if self._enable_clusters:
                cluster_summary_text = await self._run_cluster_pipeline()
            elif memory_summary:
                # 레거시 모드: 시간순 append (managed:dreaming:journal 안쪽으로)
                self.append_to_memory(memory_summary)
        except ProtectedSectionError as exc:
            # 동시 편집·외부 손상 등으로 쓰기 도중 예외. 부분 변경된 파일이 있을 수
            # 있으므로 모든 대상 파일을 백업으로 복원해 트랜잭션 의미를 보존한다.
            logger.error(
                "Dreaming aborted (mid-write): %s. 백업에서 복원합니다.", exc
            )
            self._restore_from_backups(backups)
            return None

        # 결과 산출물이 전혀 없으면 None 반환 (테스트/호출자가 빈 회차를 식별할 수 있도록).
        # active-projects만 갱신된 경우(다른 산출물이 모두 비어 있음)에도 None을 반환하지
        # 않는다 — sidecar/USER.md 에 의미 있는 변경이 일어났음을 호출자가 인지해야 한다.
        if not any(
            [memory_summary, user_insights, soul_updates, agent_updates,
             cluster_summary_text, active_projects_rendered]
        ):
            return None

        # MemoryEntry.summary는 호환을 위해 LLM이 만든 memory_summary를 우선 사용하고,
        # 클러스터 모드에서 memory_summary가 비어있다면 클러스터 요약 통합본을 담는다.
        entry_summary = memory_summary or cluster_summary_text
        return MemoryEntry(
            summary=entry_summary,
            source=f"dreaming_{datetime.now().strftime('%Y-%m-%d')}",
        )

    @staticmethod
    def _restore_from_backups(backups: list[tuple[Path, Path | None]]) -> None:
        """런타임 abort 시 모든 대상 파일을 백업본으로 되돌린다.

        백업이 없는 항목(파일이 처음부터 없었던 경우 등)은 건너뛴다 — 그런 파일은
        쓰기 시도 자체가 차단되었으므로 손상돼 있을 수 없다.
        """
        for original, backup in backups:
            if backup is None or not backup.is_file():
                continue
            try:
                shutil.copy2(backup, original)
                logger.info("Restored %s from backup %s", original, backup)
            except OSError:
                # 복원조차 실패하면 운영자 개입이 필요 — 로그에 명확히 남긴다.
                logger.exception(
                    "Failed to restore %s from backup %s — manual intervention required",
                    original,
                    backup,
                )

    async def _run_cluster_pipeline(self) -> str:
        """Phase 3 그래프형 드리밍 — 영향받은 클러스터를 LLM 요약으로 갱신하고 MEMORY.md를 upsert.

        Returns:
            이번 회차에 갱신된 클러스터 요약을 줄로 합친 텍스트(MemoryEntry용).
            영향받은 클러스터가 없으면 빈 문자열.
        """
        affected = self.assign_clusters_for_unprocessed()
        if not affected:
            return ""

        summaries: list[str] = []
        for cid, msgs in affected.items():
            cluster = self._store.get_cluster(cid)
            existing_label = cluster.label if cluster else ""
            existing_summary = cluster.summary if cluster else ""
            updated = await self.summarize_cluster(
                msgs, existing_label, existing_summary
            )
            new_label = updated.get("label", existing_label)
            new_summary = updated.get("summary", existing_summary)
            self._store.update_cluster(cid, label=new_label, summary=new_summary)
            self.upsert_memory_section(cid, new_label, new_summary)
            summaries.append(f"[cluster {cid} · {new_label}]\n{new_summary}")
        return "\n\n".join(summaries)
