"""드리밍 파이프라인: 대화 이력을 요약하여 핵심 기억(MEMORY.md)과 사용자 프로필(USER.md)을 갱신하는 모듈.

주요 동작 흐름:
1. run() 호출 시 기존 MEMORY.md / USER.md를 백업(.bak)한다.
2. 마지막 드리밍 이후 미처리 대화 메시지를 수집한다.
3. LLM에게 대화를 분석시켜 기억 요약(memory)과 사용자 인사이트(user_insights)를 추출한다.
4. 결과를 각각 MEMORY.md, USER.md에 추가(append)한다.

Phase 3(spec 005): 클러스터링이 활성화되면 MEMORY.md는 시간순 append가 아니라
 클러스터별 ``<!-- cluster:N start --> ... <!-- cluster:N end -->`` 섹션 단위로 upsert된다.
임베딩이 부착된 메시지를 ``IncrementalClusterer``로 그룹핑하고, 영향받은 클러스터마다
LLM에 (기존 요약 + 신규 메시지)를 보내 새 요약을 받아 ``semantic_clusters`` 테이블과
MEMORY.md를 함께 갱신한다. USER/SOUL/AGENT 파일은 기존 동작 그대로 유지된다.

설계 결정:
- LLM 호출 실패 시 단순 텍스트 요약(fallback)으로 대체하여 파이프라인이 중단되지 않도록 한다.
- 대화 텍스트는 8000자로 잘라 LLM 컨텍스트 초과를 방지한다.
- 백업 파일명에 타임스탬프를 포함하여 여러 번 드리밍해도 이전 백업이 덮어씌워지지 않는다.
- 클러스터링이 비활성이거나 임베딩이 전혀 없는 입력일 때는 기존 append 동작으로 자연 fallback 한다.
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

logger = logging.getLogger(__name__)

_DREAMING_PROMPT = """\
다음 대화 내역을 분석하여 다섯 가지를 JSON으로 추출하세요.

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
            insights_file: 인사이트 메타 sidecar(JSONL) 파일 경로 (BIZ-73). None이면
                ``user_file`` 옆 ``insights.jsonl`` 로 자동 결정. ``user_file``도 None이면
                인사이트 메타 추적은 비활성.
            insight_promotion_threshold: 인사이트 승격 임계 관측 횟수 (BIZ-73). 단발 관측은
                항상 confidence ≤ 0.4 로 캡되고, 이 횟수에 도달해야 승격선(0.7)에 진입한다.
                기본 3회.
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

    @property
    def insight_store(self) -> InsightStore | None:
        """인사이트 sidecar 저장소 (BIZ-73). Admin API 가 같은 sidecar 를 공유한다.

        ``insights_file`` 인자나 ``user_file`` 옆 자동 결정 경로가 둘 다 없으면
        ``None``. Admin API 라우팅은 None 일 때 503 으로 명시 disabled 응답.
        """
        return self._insights_store

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

    def collect_unprocessed_with_ids(
        self, last_dreaming: datetime | None = None
    ) -> list[tuple[int, ConversationMessage]]:
        """``collect_unprocessed`` 의 id-bearing 변형 (BIZ-77).

        인사이트 source 역추적을 위해 메시지 rowid 를 함께 수집해야 한다.
        반환 순서는 시간순 (id 오름차순) 으로 일관된다.
        """
        if last_dreaming:
            return self._store.get_since_with_ids(last_dreaming)
        return self._store.get_recent_with_ids(limit=50)

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
        """드리밍 요약을 MEMORY.md 파일 끝에 추가한다.

        파일이 없으면 '# Memory' 헤더와 함께 새로 생성한다.
        """
        if not summary:
            return

        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._memory_file.is_file():
            existing = self._memory_file.read_text(encoding="utf-8")

        if not existing.strip():
            existing = "# Memory\n"

        if not existing.endswith("\n"):
            existing += "\n"

        new_content = f"{existing}\n{summary}\n"
        self._memory_file.write_text(new_content, encoding="utf-8")
        logger.info("Updated memory file: %s", self._memory_file)

    def _update_file_section(self, file_path: Path, updates: str, section_header: str) -> None:
        """파일에 날짜별 섹션 헤더와 함께 업데이트 내용을 추가한다.

        파일이 없거나 updates가 비어있으면 아무 작업도 하지 않는다.
        """
        if not updates or not file_path:
            return

        file_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if file_path.is_file():
            existing = file_path.read_text(encoding="utf-8")

        if not existing.strip():
            existing = f"# {file_path.stem}\n"

        if not existing.endswith("\n"):
            existing += "\n"

        date_str = datetime.now().strftime("%Y-%m-%d")
        new_content = f"{existing}\n## {section_header} ({date_str})\n{updates}\n"
        file_path.write_text(new_content, encoding="utf-8")
        logger.info("Updated file: %s", file_path)

    def update_user_file(self, insights: str) -> None:
        """새로운 사용자 인사이트를 USER.md 파일에 추가한다."""
        if self._user_file:
            self._update_file_section(self._user_file, insights, "Dreaming Insights")

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
        """에이전트 성격/말투 변경을 SOUL.md 파일에 추가한다."""
        if self._soul_file:
            self._update_file_section(self._soul_file, updates, "Dreaming Updates")

    def update_agent_file(self, updates: str) -> None:
        """에이전트 행동 규칙 변경을 AGENT.md 파일에 추가한다."""
        if self._agent_file:
            self._update_file_section(self._agent_file, updates, "Dreaming Updates")

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

        규칙:
        - 마커가 이미 존재하면 그 사이 본문만 교체(외부 영역은 보존).
        - 마커가 없으면 파일 끝에 새 섹션 append.
        - 파일이 없으면 ``# Memory`` 헤더와 함께 새로 생성.
        """
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if self._memory_file.is_file():
            existing = self._memory_file.read_text(encoding="utf-8")
        if not existing.strip():
            existing = "# Memory\n"
        if not existing.endswith("\n"):
            existing += "\n"

        section_body = self._format_cluster_section_body(cluster_id, label, summary)
        start_marker = _CLUSTER_MARKER_START.format(cid=cluster_id)
        end_marker = _CLUSTER_MARKER_END.format(cid=cluster_id)
        new_block = f"{start_marker}\n{section_body}\n{end_marker}"

        # 정규식 매칭은 동일 cluster_id의 시작/끝 마커 한 쌍을 정확히 잡아낸다.
        section_re = re.compile(
            rf"{re.escape(start_marker)}\n?.*?\n?{re.escape(end_marker)}",
            re.DOTALL,
        )
        if section_re.search(existing):
            updated = section_re.sub(new_block, existing, count=1)
        else:
            # 신규 섹션 — 끝에 빈 줄 하나를 두고 append
            sep = "" if existing.endswith("\n\n") else "\n"
            updated = f"{existing}{sep}{new_block}\n"

        self._memory_file.write_text(updated, encoding="utf-8")
        logger.info("Upserted cluster %d in memory file", cluster_id)

    @staticmethod
    def _format_cluster_section_body(
        cluster_id: int, label: str, summary: str
    ) -> str:
        """클러스터 섹션 본문을 사람이 읽기 좋은 마크다운으로 포맷한다."""
        header_label = label.strip() or f"cluster {cluster_id}"
        body = summary.strip() or "(no summary yet)"
        return f"## {header_label} (cluster {cluster_id})\n\n{body}"

    async def run(self, last_dreaming: datetime | None = None) -> MemoryEntry | None:
        """전체 드리밍 파이프라인을 실행한다.

        1. 미처리 대화 메시지를 수집한다.
        2. 처리할 내용이 있으면 대상 파일들을 백업한다.
        3. LLM을 통해 요약을 생성한다 (USER/SOUL/AGENT 갱신용).
        4. ``_enable_clusters=True``면 그래프형 드리밍을 추가로 실행하여
           MEMORY.md를 시간순 append 대신 클러스터별 마커 섹션 upsert로 갱신한다.
           False(기본값)면 기존 append 동작을 유지한다.
        5. USER/SOUL/AGENT 갱신은 두 모드 모두 동일하게 수행한다.

        Args:
            last_dreaming: 마지막 드리밍 시각. None이면 최근 메시지를 대상으로 한다.

        Returns:
            생성된 MemoryEntry 객체. 처리할 메시지가 없거나 결과가 비어있으면 None.
        """
        # BIZ-77 — 메시지를 id 와 함께 수집한다. 분석 자체는 message 객체만 쓰지만
        # 인사이트 source 역추적을 위해 rowid 를 sidecar 에 기록해야 하기 때문이다.
        id_pairs = self.collect_unprocessed_with_ids(last_dreaming)
        if not id_pairs:
            logger.info("No new messages to process for dreaming.")
            return None
        source_msg_ids = [mid for mid, _ in id_pairs]
        messages = [msg for _, msg in id_pairs]

        # 처리할 메시지가 있을 때만 백업 생성
        self.create_backup(self._memory_file)
        if self._user_file:
            self.create_backup(self._user_file)
        if self._soul_file:
            self.create_backup(self._soul_file)
        if self._agent_file:
            self.create_backup(self._agent_file)

        result = await self.summarize(messages)
        memory_summary = result.get("memory", "")
        user_insights = result.get("user_insights", "")
        user_insights_meta = result.get("user_insights_meta", []) or []
        soul_updates = result.get("soul_updates", "")
        agent_updates = result.get("agent_updates", "")

        # BIZ-73 + BIZ-77: 인사이트 메타 sidecar 갱신 — USER.md 본문 append 보다 먼저
        # 실행하여 어떤 항목이 "승격" 됐는지(USER.md에 high-confidence 표시 가능)
        # 사전 판단할 수 있게 한다. BIZ-77 부터는 이번 회차에 분석된 모든 메시지의
        # rowid 를 신규/강화된 모든 인사이트에 부착한다 — Admin "근거 보기" 의 입력.
        promoted_meta: list[InsightMeta] = []
        if user_insights_meta and self._insights_store:
            _, promoted_meta = self.apply_insight_meta(
                user_insights_meta, source_msg_ids=source_msg_ids
            )

        # USER/SOUL/AGENT는 두 모드 공통으로 갱신
        if user_insights:
            self.update_user_file(user_insights)
        if soul_updates:
            self.update_soul_file(soul_updates)
        if agent_updates:
            self.update_agent_file(agent_updates)

        # MEMORY.md 갱신은 클러스터 모드 여부에 따라 분기
        cluster_summary_text = ""
        if self._enable_clusters:
            cluster_summary_text = await self._run_cluster_pipeline()

        if not self._enable_clusters and memory_summary:
            # 레거시 모드: 시간순 append
            self.append_to_memory(memory_summary)

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
