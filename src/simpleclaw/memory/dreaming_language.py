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

async def summarize(self, messages: list) -> dict:
    """LLM을 사용하여 대화 요약을 생성한다 — BIZ-299 부터는 파일별 다회 호출 오케스트레이터.

    라우터가 있으면 memory/user/soul/agent/active_projects 각각의 YAML 프롬프트를
    로드해 별도 호출을 발사한다 (BIZ-299 §1). 호출 중 하나라도 실패하면 ``run`` 의
    outer try/except 가 잡아 사이클 자체를 abort 한다 — fail-closed 시맨틱.

    라우터가 없으면 단순 텍스트 폴백을 사용한다 (회귀 호환 — 기존 ``test_dreaming``
    의 no-router 경로).

    Args:
        messages: 요약 대상 대화 메시지 리스트.

    Returns:
        ``memory`` / ``user_insights`` / ``user_insights_meta`` / ``soul_updates`` /
        ``agent_updates`` / ``active_projects`` 6 키를 포함하는 딕셔너리.
        모든 키는 항상 존재하며, 빈 값은 각 타입의 기본값(빈 문자열 또는 빈 리스트).
    """
    # BIZ-299 — 파일별 메트릭은 매 호출 사이클마다 초기화. ``run`` 이 종료 시점에
    # ``run_record.details["per_file"]`` 로 영속한다.
    self._per_file_metrics = {}

    if not messages:
        return {
            "memory": "",
            "user_insights": "",
            "user_insights_meta": [],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        }

    if not self._router:
        # 라우터 부재 — 폴백 텍스트로 memory 만 채우고 나머지는 빈 값 (기존 동작 호환).
        return {
            "memory": self._summarize_fallback(messages),
            "user_insights": "",
            "user_insights_meta": [],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        }

    # BIZ-299 §3 — 1차는 순차 실행. ``asyncio.gather`` 병렬화는 follow-up sub-issue
    # 에서 rate-limit 정책 측정 후 도입.
    mem_part = await self.summarize_memory(messages)
    user_part = await self.summarize_user(messages)
    soul_part = await self.summarize_soul(messages)
    agent_part = await self.summarize_agent(messages)
    ap_part = await self.summarize_active_projects(messages)

    merged: dict = {
        "memory": mem_part.get("memory", ""),
        "user_insights": user_part.get("user_insights", ""),
        "user_insights_meta": user_part.get("user_insights_meta", []) or [],
        "soul_updates": soul_part.get("soul_updates", ""),
        "agent_updates": agent_part.get("agent_updates", ""),
        "active_projects": ap_part.get("active_projects", []) or [],
    }

    # BIZ-80: LLM 산출물에 1차 언어 정책 강제 적용. 정책 비활성이면 입력을 그대로 통과.
    return self._enforce_language_policy(merged)

async def summarize_memory(self, messages: list) -> dict:
    """MEMORY.md (journal) 갱신용 LLM 호출 (BIZ-299).

    Returns:
        ``{"memory": str}`` — 오늘 날짜 헤더 + bullet 본문. 비어 있으면 빈 문자열.
    """
    raw = await self._call_dreaming_llm(
        prompt_name="memory",
        prompt_vars={
            "language_instruction": language_instruction_block(self._language_policy),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "conversations": self._format_conversations(messages),
        },
        max_tokens_key="memory",
    )
    result = self._extract_json_object(raw)
    return {"memory": result.get("memory", "") if isinstance(result, dict) else ""}

async def summarize_user(self, messages: list) -> dict:
    """USER.md insights 갱신용 LLM 호출 (BIZ-299).

    Returns:
        ``{"user_insights": str, "user_insights_meta": list[dict]}``.
        ``user_insights_meta`` 는 ``{"topic": str, "text": str}`` 객체 배열 — 형식
        오류 항목은 silently drop.
    """
    raw = await self._call_dreaming_llm(
        prompt_name="user",
        prompt_vars={
            "language_instruction": language_instruction_block(self._language_policy),
            "existing_user_md": self._read_existing(self._user_file),
            "conversations": self._format_conversations(messages),
        },
        max_tokens_key="user",
    )
    result = self._extract_json_object(raw)
    if not isinstance(result, dict):
        return {"user_insights": "", "user_insights_meta": []}
    return {
        "user_insights": result.get("user_insights", "") or "",
        "user_insights_meta": _coerce_meta_items(result.get("user_insights_meta")),
    }

async def summarize_soul(self, messages: list) -> dict:
    """SOUL.md 갱신용 LLM 호출 (BIZ-299).

    Returns:
        ``{"soul_updates": str}`` — 사용자가 명시적으로 지시한 성격/말투 변경 bullet.
    """
    raw = await self._call_dreaming_llm(
        prompt_name="soul",
        prompt_vars={
            "language_instruction": language_instruction_block(self._language_policy),
            "existing_soul_md": self._read_existing(self._soul_file),
            "conversations": self._format_conversations(messages),
        },
        max_tokens_key="soul",
    )
    result = self._extract_json_object(raw)
    return {"soul_updates": (result.get("soul_updates") or "") if isinstance(result, dict) else ""}

async def summarize_agent(self, messages: list) -> dict:
    """AGENT.md 갱신용 LLM 호출 (BIZ-299).

    Returns:
        ``{"agent_updates": str}`` — 사용자가 명시적으로 지시한 행동/도구 설정 변경.
    """
    raw = await self._call_dreaming_llm(
        prompt_name="agent",
        prompt_vars={
            "language_instruction": language_instruction_block(self._language_policy),
            "existing_agent_md": self._read_existing(self._agent_file),
            "conversations": self._format_conversations(messages),
        },
        max_tokens_key="agent",
    )
    result = self._extract_json_object(raw)
    return {"agent_updates": (result.get("agent_updates") or "") if isinstance(result, dict) else ""}

async def summarize_active_projects(self, messages: list) -> dict:
    """USER.md active-projects 섹션 갱신용 LLM 호출 (BIZ-299).

    Returns:
        ``{"active_projects": list[dict]}`` — ``{"name", "role", "recent_summary"}``
        객체 배열. 잘못된 타입은 빈 리스트로 강등 (다른 산출물까지 같이 잃지 않도록).
    """
    raw = await self._call_dreaming_llm(
        prompt_name="active_projects",
        prompt_vars={
            "language_instruction": language_instruction_block(self._language_policy),
            "existing_user_md": self._read_existing(self._user_file),
            "conversations": self._format_conversations(messages),
        },
        # ``active_projects`` 키가 없으면 ``user`` 캡으로 떨어뜨려 본다 — USER.md
        # 산출물이라 의미상 동일한 길이 경향.
        max_tokens_key="active_projects",
        max_tokens_fallback_key="user",
    )
    result = self._extract_json_object(raw)
    if not isinstance(result, dict):
        return {"active_projects": []}
    raw_projects = result.get("active_projects")
    if not isinstance(raw_projects, list):
        if raw_projects is not None:
            logger.warning(
                "active_projects field is not a list (got %s); ignoring",
                type(raw_projects).__name__,
            )
        raw_projects = []
    return {"active_projects": raw_projects}

async def _call_dreaming_llm(
    self,
    *,
    prompt_name: str,
    prompt_vars: dict,
    max_tokens_key: str,
    max_tokens_fallback_key: str | None = None,
) -> str:
    """파일별 dreaming LLM 호출의 단일 진입점 (BIZ-299).

    - YAML 프롬프트 로더(BIZ-298) 로 ``{prompt_name}.yaml`` 을 가져와 ``system_prompt``
      와 ``user_prompt`` 를 구성한다.
    - ``LLMRequest.max_tokens`` (BIZ-297) 에 운영자 config 의 ``dreaming.max_tokens.{key}``
      값을 박는다. ``None`` / 누락이면 프로바이더 기본값으로 fallback.
    - 호출 시간/토큰 사용량을 ``self._per_file_metrics[prompt_name]`` 에 기록 —
      ``DreamingRunStore`` 가 행 메타로 영속한다.

    호출 자체의 예외는 호출자(``summarize_*``) 로 그대로 전파한다 — ``run()`` 의
    outer try/except 가 사이클 abort + 메트릭 error 기록을 책임진다.
    """
    max_tokens_used_key = max_tokens_key
    if (
        self._max_tokens.get(max_tokens_key) is None
        and max_tokens_fallback_key is not None
        and self._max_tokens.get(max_tokens_fallback_key) is not None
    ):
        max_tokens_used_key = max_tokens_fallback_key
    return await self._call_dreaming_llm_for_key(
        prompt_name=prompt_name,
        prompt_vars=prompt_vars,
        max_tokens_key=max_tokens_used_key,
        metric_key=prompt_name,
    )

def _format_conversations(messages: list) -> str:
    """대화 메시지 리스트를 LLM 입력 문자열로 직렬화한다.

    BIZ-299 — 기존 ``[:8000]`` 하드 truncation 을 *제거*. 누락 기간 backlog 가
    길더라도 입력은 그대로 흘려보내고 모델 자체의 컨텍스트 한계에 의존한다.
    (적응형 chunking / catch-up 윈도우 클램프는 별도 후속 sub-issue.)
    """
    lines = [f"[{msg.role.value.upper()}] {msg.content}" for msg in messages]
    return "\n".join(lines)

def _parse_llm_result(self, raw: str) -> dict:
    """레거시 6-필드 dreaming 응답 파서 — BIZ-299 이전 단일 호출 시그니처 보존용.

    BIZ-299 부터 dreaming 은 파일별 다회 호출로 분리됐고, 각 파일은 자기 키만
    파싱한다. 본 메서드는 기존 단위 테스트 (``test_dreaming.py`` 의
    ``test_parse_llm_result_*``) 와 외부 검수 스크립트 호환을 위해 6 필드 합본
    dict 를 반환한다. 신규 코드는 파일별 ``summarize_*`` 를 직접 호출하라.
    """
    obj = self._extract_json_object(raw)
    if obj is None:
        return {
            "memory": raw[:500],
            "user_insights": "",
            "user_insights_meta": [],
            "soul_updates": "",
            "agent_updates": "",
            "active_projects": [],
        }
    raw_projects = obj.get("active_projects")
    if not isinstance(raw_projects, list):
        if raw_projects is not None:
            logger.warning(
                "active_projects field is not a list (got %s); ignoring",
                type(raw_projects).__name__,
            )
        raw_projects = []
    return {
        "memory": obj.get("memory", "") or "",
        "user_insights": obj.get("user_insights", "") or "",
        "user_insights_meta": _coerce_meta_items(obj.get("user_insights_meta")),
        "soul_updates": obj.get("soul_updates", "") or "",
        "agent_updates": obj.get("agent_updates", "") or "",
        "active_projects": raw_projects,
    }

def _extract_json_object(raw: str) -> dict | None:
    """LLM 응답 텍스트에서 JSON 객체를 추출한다.

    BIZ-299 — 파일별 호출이 각자의 파싱을 가지지 않도록 한 곳에서 처리. 마크다운
    코드블록(``` ```)으로 감싼 경우와 JSON 파싱 실패 케이스 모두 처리하며, 실패
    시 ``None`` 을 반환해 호출자가 빈 결과로 떨어뜨릴 수 있게 한다.
    """
    if not raw:
        return None
    text = raw
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            text = text.removeprefix("json")
            text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse dreaming JSON: %s", raw[:200])
        return None
    if not isinstance(obj, dict):
        return None
    return obj

def _enforce_language_policy(self, result: dict) -> dict:
    """BIZ-80 — 추출된 dreaming 산출물에 1차 언어 정책을 적용한다.

    파일별 1차 언어와 다른 본문은 *드롭* 한다 (``LanguagePolicy.drop_on_violation``
    기본 True). 자동 번역은 본 단계에서 하지 않는다 — 번역은 결정성/재현성이
    떨어지고 LLM 호출 비용이 두 배로 든다. 운영자가 1회성으로 기존 파일을
    한국어로 통일하고 싶을 때는 ``scripts/migrate_dreaming_language.py`` 를 쓴다.

    파일별 매핑:
    - ``memory`` → MEMORY.md (file_kind=``"memory"``)
    - ``user_insights`` / ``user_insights_meta`` → USER.md (file_kind=``"user"``)
    - ``soul_updates`` → SOUL.md (file_kind=``"soul"``)
    - ``agent_updates`` → AGENT.md (file_kind=``"agent"``)
    - ``active_projects`` → USER.md active-projects 섹션 (file_kind=``"user"``)
    """
    policy = self._language_policy
    if policy.primary is None:
        return result

    new_result = dict(result)
    ratio = policy.min_ratio

    # MEMORY.md (시간순 journal) — bullet 단위 필터.
    memory_lang = policy.language_for("memory")
    if memory_lang and result.get("memory"):
        kept, dropped = filter_text_to_primary(
            result["memory"], memory_lang, min_ratio=ratio
        )
        if dropped:
            logger.info(
                "Language policy: dropped %d non-%s memory bullet(s): %s",
                len(dropped), memory_lang, dropped,
            )
        new_result["memory"] = kept

    user_lang = policy.language_for("user")

    # USER.md user_insights — bullet 단위 필터.
    if user_lang and result.get("user_insights"):
        kept, dropped = filter_text_to_primary(
            result["user_insights"], user_lang, min_ratio=ratio
        )
        if dropped:
            logger.info(
                "Language policy: dropped %d non-%s user_insights bullet(s): %s",
                len(dropped), user_lang, dropped,
            )
        new_result["user_insights"] = kept

    # USER.md user_insights_meta — sidecar 추적용 구조화 입력. topic/text 둘 다 검사.
    meta_items = result.get("user_insights_meta") or []
    if user_lang and meta_items:
        kept_meta, dropped_meta = filter_meta_items(
            meta_items, user_lang, min_ratio=ratio
        )
        if dropped_meta:
            logger.info(
                "Language policy: dropped %d non-%s insight meta item(s): %s",
                len(dropped_meta),
                user_lang,
                [d.get("topic") for d in dropped_meta],
            )
        new_result["user_insights_meta"] = kept_meta

    # USER.md active-projects — role/recent_summary 검사. name 은 고유명사일 수
    # 있으므로 검사하지 않는다.
    projects = result.get("active_projects") or []
    if user_lang and projects:
        kept_projects, dropped_projects = filter_active_projects(
            projects, user_lang, min_ratio=ratio
        )
        if dropped_projects:
            logger.info(
                "Language policy: dropped %d non-%s active project(s): %s",
                len(dropped_projects),
                user_lang,
                [p.get("name") for p in dropped_projects],
            )
        new_result["active_projects"] = kept_projects

    # SOUL.md / AGENT.md updates — bullet 단위 필터(자유 텍스트일 수도 있음).
    soul_lang = policy.language_for("soul")
    if soul_lang and result.get("soul_updates"):
        kept, dropped = filter_text_to_primary(
            result["soul_updates"], soul_lang, min_ratio=ratio
        )
        if dropped:
            logger.info(
                "Language policy: dropped %d non-%s soul_updates bullet(s): %s",
                len(dropped), soul_lang, dropped,
            )
        new_result["soul_updates"] = kept

    agent_lang = policy.language_for("agent")
    if agent_lang and result.get("agent_updates"):
        kept, dropped = filter_text_to_primary(
            result["agent_updates"], agent_lang, min_ratio=ratio
        )
        if dropped:
            logger.info(
                "Language policy: dropped %d non-%s agent_updates bullet(s): %s",
                len(dropped), agent_lang, dropped,
            )
        new_result["agent_updates"] = kept

    return new_result

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

