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
from datetime import datetime
from pathlib import Path

from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.insights import (
    InsightMeta,
    InsightStore,
    is_promoted,
    merge_insights,
)
from simpleclaw.memory.models import (
    ClusterRecord,
    ConversationMessage,
    MemoryEntry,
)
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.protected_section import (
    ProtectedSectionError,
    ProtectedSectionMissing,
    append_to_section,
    get_section_body,
    replace_section_body,
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

## 기존 SOUL.md 내용
{existing_soul_md}

## 기존 AGENT.md 내용
{existing_agent_md}

## 기존 USER.md 내용
{existing_user_md}

## 대화 내역
{conversations}

JSON 형식으로만 응답하세요:
{{"memory": "## {date}\\n- 항목1", "user_insights": "- 새 정보1", "user_insights_meta": [{{"topic": "주제키", "text": "새 정보1"}}], "soul_updates": "- 변경1", "agent_updates": "- 변경1"}}"""


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
        insights_file: str | Path | None = None,
        insight_promotion_threshold: int = 3,
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
        """
        if last_dreaming:
            return self._store.get_since(last_dreaming)
        return self._store.get_recent(limit=50)

    async def summarize(self, messages: list) -> dict:
        """LLM을 사용하여 대화 요약을 생성한다.

        LLM 호출이 실패하거나 라우터가 없으면 단순 텍스트 요약으로 폴백한다.

        Args:
            messages: 요약 대상 대화 메시지 리스트.

        Returns:
            'memory'와 'user_insights' 키를 포함하는 딕셔너리.
        """
        if not messages:
            return {"memory": "", "user_insights": "", "user_insights_meta": []}

        if self._router:
            try:
                return await self._summarize_with_llm(messages)
            except Exception:
                logger.exception("LLM summarization failed, using fallback")

        return {
            "memory": self._summarize_fallback(messages),
            "user_insights": "",
            "user_insights_meta": [],
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
            return {
                "memory": result.get("memory", ""),
                "user_insights": result.get("user_insights", ""),
                "user_insights_meta": meta_items,
                "soul_updates": result.get("soul_updates", ""),
                "agent_updates": result.get("agent_updates", ""),
            }
        except json.JSONDecodeError:
            logger.warning("Failed to parse dreaming JSON: %s", raw[:200])
            return {
                "memory": raw[:500],
                "user_insights": "",
                "user_insights_meta": [],
                "soul_updates": "",
                "agent_updates": "",
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

    def apply_insight_meta(
        self,
        meta_items: list[dict],
        source_msg_ids: list[int] | None = None,
    ) -> tuple[list[InsightMeta], list[InsightMeta]]:
        """이번 회차의 인사이트 메타를 sidecar 와 병합·저장한다.

        Args:
            meta_items: ``[{"topic": ..., "text": ...}, ...]`` 형태의 LLM 추출물.
            source_msg_ids: 이번 회차에 분석한 메시지 rowid 목록(F: source linkage 의 1차 입력).
                None 이면 빈 리스트로 처리. 이 회차에 신규/갱신된 모든 인사이트에 동일하게 부착된다.

        Returns:
            (changed, promoted)
            - changed: 이번 회차에 추가/갱신된 인사이트 (모두).
            - promoted: 그 중 ``is_promoted == True`` 인 항목들 (USER.md 노출 대상).
            sidecar 저장소가 비활성이거나 입력이 비어있으면 (빈 리스트, 빈 리스트).
        """
        if not self._insights_store or not meta_items:
            return [], []

        now = datetime.now()
        ids = list(source_msg_ids or [])
        observations: list[InsightMeta] = []
        for item in meta_items:
            topic = (item.get("topic") or "").strip()
            text = (item.get("text") or "").strip()
            if not topic or not text:
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

        if not observations:
            return [], []

        existing = self._insights_store.load()
        merged, changed = merge_insights(
            existing, observations, self._insight_promotion_threshold
        )
        self._insights_store.save_all(merged)

        promoted = [
            m for m in changed
            if is_promoted(m, self._insight_promotion_threshold)
        ]
        logger.info(
            "Insights updated: %d changed, %d promoted (threshold=%d)",
            len(changed), len(promoted), self._insight_promotion_threshold,
        )
        return changed, promoted

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
        messages = self.collect_unprocessed(last_dreaming)
        if not messages:
            logger.info("No new messages to process for dreaming.")
            return None

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

        # BIZ-73: 인사이트 메타 sidecar 갱신 — USER.md 본문 append 보다 먼저 실행하여
        # 어떤 항목이 "승격" 됐는지(USER.md에 high-confidence 표시 가능) 사전 판단할 수 있게 한다.
        # 후속 BIZ-77(F: source linkage) 가 source_msg_ids 를 풍부하게 채울 예정.
        # 주의: sidecar 갱신은 try 바깥에서 수행하지만, fail-closed 의미를 깨지 않기 위해
        # 어떤 markdown 파일도 아직 변경되지 않았다(preflight 통과 직후). sidecar 자체는
        # JSONL atomic-rename 으로 항상 일관된 상태가 보장된다.
        promoted_meta: list[InsightMeta] = []
        if user_insights_meta and self._insights_store:
            _, promoted_meta = self.apply_insight_meta(
                user_insights_meta, source_msg_ids=[]
            )

        cluster_summary_text = ""
        try:
            # USER/SOUL/AGENT는 두 모드 공통으로 갱신
            if user_insights:
                self.update_user_file(user_insights)
            if soul_updates:
                self.update_soul_file(soul_updates)
            if agent_updates:
                self.update_agent_file(agent_updates)

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

        # 결과 산출물이 전혀 없으면 None 반환 (테스트/호출자가 빈 회차를 식별할 수 있도록)
        if not any(
            [memory_summary, user_insights, soul_updates, agent_updates, cluster_summary_text]
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
