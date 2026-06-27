"""Agent orchestrator — 페르소나·스킬·메모리·LLM을 하나로 묶는 중앙 조율기.

응답 파이프라인 (Native Function Calling):
1. 사용자 메시지 수신
2. LLM에 도구 정의(tools)와 함께 메시지 전송
3. LLM이 tool_calls를 반환하면 → 도구 실행 → 결과를 메시지에 추가 → 재호출
4. LLM이 텍스트만 반환하면 → 최종 응답으로 반환

Hot-reload 정책:
  AGENT.md, USER.md, MEMORY.md, 스킬/레시피 파일은 매 메시지(process_message /
  process_cron_message) 진입 시 1회 디스크에서 다시 읽는다.
  → 파일 수정 후 봇 리스타트 없이 다음 메시지부터 반영됨.
  → tool loop 내부에서는 캐시된 값을 재사용하여 불필요한 I/O를 방지함.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.config import (
    load_agent_config,
    load_asset_selection_config,
    load_daemon_config,
    load_memory_config,
    load_persona_config,
    load_recipes_config,
    load_security_config,
    load_skills_learning_config,
)
from simpleclaw.llm.models import (
    LLMRequest,
    MultimodalAttachment,
    SystemBlock,
    ToolCall,
)
from simpleclaw.llm.providers.base import TextDeltaCallback
from simpleclaw.llm.router import create_router
from simpleclaw.logging.trace_context import trace_scope
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.embedding_service import EmbeddingService
from simpleclaw.memory.models import (
    CHANNEL_CRON_ADMIN,
    CHANNEL_GOAL_PREFIX,
    CHANNEL_RECIPE_PREFIX,
    ConversationMessage,
    MessageRole,
)
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.proactive.conversation_detector import ConversationEndDetector
from simpleclaw.proactive.store import OpportunityStore
from simpleclaw.security import CommandGuard
from simpleclaw.security.secrets import default_manager
from simpleclaw.security.sanitize import sanitize_tool_output
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.learning import (
    SkillSuggestion,
    SkillSuggestionStore,
    build_skill_candidate_prompt,
    is_complex_successful_trace,
    snapshots_from_trace,
    suggestion_from_candidate_payload,
    trace_fingerprint,
)
from simpleclaw.skills.models import SkillDefinition

from simpleclaw.agent import (
    command_dispatch,
    memory_search,
    skill_dispatch,
    tool_dispatch,
)
from simpleclaw.agent.asset_selector import (
    AssetSelectionResult,
    build_selector_assets,
    build_selector_prompt,
    build_selector_tool_definition,
    filter_assets_by_selection,
    normalize_selector_response,
)
from simpleclaw.agent.clarify import ClarifyRequest, clarify_chat_id_var
from simpleclaw.agent.commands import (
    parse_goal_command,
    try_cron_command,
    try_recipe_command,
)
from simpleclaw.agent.goal_loop import GoalLoopConfig, GoalLoopRunner
from simpleclaw.agent.context_retrieval import (
    ContextRetrievalConfig,
    ContextRetrievalService,
)
from simpleclaw.agent.progress import ProgressCallback
from simpleclaw.agent.response_router import (
    ResponseRoute,
    classify_response_route,
)
from simpleclaw.agent.tool_loop import (
    ToolLoopResult,
    ToolLoopRunner,
    ToolLoopState,
)
from simpleclaw.agent.file_mutation_tracker import (
    FileMutationTracker,
    TrackedRoot,
)
from simpleclaw.agent.tool_schemas import (
    ToolScope,
    build_tool_definitions,
    validate_dispatch_tool_names,
)
from simpleclaw.agent.system_prompts import load_system_prompt

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler
    from simpleclaw.logging.metrics import MetricsCollector
    from simpleclaw.logging.structured_logger import StructuredLogger

logger = logging.getLogger(__name__)

_ATTACHMENT_CONTEXT_HEADER = "Attachment context"

_NATIVE_DISPATCH_TOOL_NAMES = frozenset({
    "cli",
    "web_fetch",
    "web_search",
    "file_read",
    "file_write",
    "file_manage",
    "skill_docs",
    "search_memory",
    "clarify",
    "cron",
    "runtime_status",
    "config_inspect",
    "log_debug",
    "asset_inventory",
    "deploy_status",
    "recipe_validate",
    "skill_validate",
    "restart_runtime",
    "skill_learning",
})
validate_dispatch_tool_names(
    _NATIVE_DISPATCH_TOOL_NAMES,
    scopes=(ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT),
    operator_gate=True,
)


def _inject_env_secret_refs(env_secret_refs: object) -> None:
    """config의 시크릿 참조를 스킬 실행용 환경변수로 주입한다.

    런타임 스킬은 기존 CLI 생태계와 호환되도록 API 키를 환경변수로 읽는 경우가
    많다. 평문 config/LaunchAgent 대신 암호화 vault에는 ``file:<name>`` 참조를
    저장하고, 봇 프로세스 시작 시 필요한 키만 ``os.environ``에 복원한다.
    실제 자식 프로세스 전달 여부는 ``security.env_passthrough``가 별도로 제어한다.
    """
    if not isinstance(env_secret_refs, dict):
        return

    manager = default_manager()
    for env_name, ref in env_secret_refs.items():
        if not isinstance(env_name, str) or not env_name:
            continue
        if not isinstance(ref, str) or not ref:
            continue
        value = manager.resolve(ref)
        if not value:
            logger.warning("Configured env secret could not be resolved: %s", env_name)
            continue
        os.environ[env_name] = value

# 시스템 프롬프트에 추가할 도구 사용 안내.
#
# 운영 지침에 따라 하드코딩 대신 ``prompts/system/tool_usage.yaml`` 을
# 단일 Source of Truth 로 사용한다.
_TOOL_USAGE_INSTRUCTION = load_system_prompt("tool_usage").prompt

# BIZ-160 — tool 루프가 max_tool_iterations 를 다 쓰고도 LLM 이 빈 텍스트를 돌려준
# 사고(2026-05-08)에서 사용자에게 아무 메시지도 가지 않아 봇이 죽은 것처럼 보였음.
# 빈 응답 자리에 안내 메시지를 채워, 채널 라우터(`if response:`)가 sendMessage 를
# skip 하지 않도록 한다.
_BUDGET_EXHAUSTED_EMPTY_MESSAGE = (
    "여러 도구를 시도했지만 답을 마무리하지 못했습니다.\n"
    "질문을 짧게 다시 표현해 주시거나, URL/파일 경로를 함께 알려 주시면 도움이 됩니다.\n"
    "(debug: tool loop {iterations}회 반복 후 종료)"
)
_BUDGET_EXHAUSTED_HINT_SUFFIX = (
    "(참고: 도구 호출 한도 {iterations}회에 도달해 추가 정보 수집을 멈췄습니다)"
)
_EMPTY_DIRECT_RESPONSE_MESSAGE = (
    "빈 응답으로 인해 응답을 생성하지 못했습니다. 죄송하지만 한 번 더 말씀해 주세요."
)
_UNDO_USAGE_MESSAGE = "사용법: /undo 또는 /undo N (N은 1 이상의 정수)"
_UNDO_NO_TURNS_MESSAGE = "되돌릴 대화 턴이 없습니다."
_UNDO_SUCCESS_MESSAGE = (
    "최근 {turns}턴을 다음 응답부터 제외했습니다. "
    "원본 메시지는 감사용으로 DB에 남겨 두며, 이 /undo 명령 자체는 대화 이력에 저장하지 않습니다."
)
_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE = (
    "확인해 봤지만 관련 기록을 찾지 못했습니다."
)
_TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE = (
    "확인 중 오류가 발생해 답변을 마무리하지 못했습니다: {detail}"
)
_TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE = (
    "확인은 했지만 답변을 마무리하지 못했습니다. 확인한 결과: {detail}"
)
_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS = (
    "0 chars",
    "0 rows",
    "0 row",
    "no rows",
    "no row",
    "no results",
    "not found",
    "검색 결과가 없습니다",
    "결과 없음",
    "없음",
    "없습니다",
    "못 찾",
    "찾지 못",
)
_TOOL_RESULT_EMPTY_FINAL_ERROR_PREFIXES = (
    "error",
    "traceback",
    "exception",
    "timeout",
    "failed",
    "command failed",
    "tool error",
    "오류",
    "실패",
)

_REALTIME_LOOKUP_SKILL_NAME = "realtime-lookup-skill"
_REALTIME_LOOKUP_CONTEXT_HEADER = "## Realtime Lookup Evidence"
_LIVE_FACT_TIME_CUES = (
    "오늘",
    "현재",
    "지금",
    "실시간",
    "방금",
    "최신",
    "결과",
    "스코어",
    "예보",
    "마감",
    "장마감",
)
_LIVE_FACT_CORRECTION_CUES = (
    "틀렸",
    "이상해",
    "다시 확인",
    "확인해",
    "맞아?",
)
_LIVE_FACT_SPORTS_TERMS = (
    "프로야구",
    "kbo",
    "야구",
    "축구",
    "농구",
    "배구",
    "경기 결과",
    "스코어",
)
_LIVE_FACT_STOCK_TERMS = (
    "주가",
    "주식",
    "코스피",
    "코스닥",
    "나스닥",
    "다우",
    "s&p",
    "환율",
    "증시",
    "시장 마감",
    "티커",
)
_LIVE_FACT_WEATHER_TERMS = (
    "날씨",
    "기온",
    "강수",
    "비 와",
    "눈 와",
    "미세먼지",
    "예보",
)
_LIVE_FACT_NEWS_TERMS = (
    "뉴스",
    "속보",
    "기사",
    "최신 소식",
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    """소문자화된 텍스트에서 키워드가 하나라도 보이는지 확인한다."""
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _looks_like_live_fact_request(text: str, prior_context: str = "") -> bool:
    """경기·주가·날씨·뉴스처럼 웹 근거가 필요한 최신 사실 질문인지 판정한다.

    모델 프롬프트 가드만으로는 작은 모델이 실시간 질문을 바로 답하는 회귀를
    막지 못했다. 그래서 보수적인 키워드 게이트로 최종 답변 직전에도 한 번 더
    차단한다. 단순 스포츠 규칙 설명 같은 비실시간 질문은 시간 cue 없이 통과시킨다.
    """
    if not text.strip():
        return False
    has_time_cue = _contains_any(text, _LIVE_FACT_TIME_CUES)
    if _contains_any(text, _LIVE_FACT_WEATHER_TERMS):
        return True
    if _contains_any(text, _LIVE_FACT_NEWS_TERMS) and has_time_cue:
        return True
    if _contains_any(text, _LIVE_FACT_STOCK_TERMS):
        return True
    if has_time_cue and _contains_any(text, _LIVE_FACT_SPORTS_TERMS):
        return True
    if _contains_any(text, _LIVE_FACT_CORRECTION_CUES):
        context = prior_context[-3000:]
        return _looks_like_live_fact_request(context, prior_context="")
    return False


def _parse_undo_command(text: str) -> tuple[bool, int | None]:
    """/undo 명령 여부와 요청 turn 수를 파싱한다.

    Telegram은 slash command를 일반 텍스트로 전달하므로 LLM/tool loop에 넣기 전
    오케스트레이터에서 선처리한다. ``/undo``의 기본값은 1이고, ``/undo N``만
    허용한다. 그 외 토큰/음수/0은 사용법 안내로 돌린다.
    """
    parts = text.strip().split()
    if not parts or parts[0] != "/undo":
        return False, None
    if len(parts) == 1:
        return True, 1
    if len(parts) != 2:
        return True, None
    try:
        turns = int(parts[1])
    except ValueError:
        return True, None
    if turns < 1:
        return True, None
    return True, turns


def _realtime_lookup_skill_payload(
    text: str,
    now_kst: object,
    prior_context: str = "",
) -> str | None:
    """실시간성 질문을 evidence 스킬용 단일 토큰 payload로 직렬화한다.

    BIZ-359: Gemini는 모델이 직접 반환하지 않은 synthetic assistant
    functionCall을 다음 요청 history에 넣으면 ``thought_signature`` 누락으로 거부한다.
    그래서 오케스트레이터는 더 이상 강제 ``web_fetch`` tool call을 합성하지 않고,
    별도 ``realtime-lookup-skill``을 LLM history 밖에서 먼저 실행한 뒤 그 결과만
    system evidence 블록으로 주입한다. 스킬 executor가 args를 공백 split하므로
    JSON은 URL-safe base64 단일 토큰으로 전달한다.
    """
    if not _looks_like_live_fact_request(text, prior_context=prior_context):
        return None

    normalized_query = " ".join(text.split()) or "실시간 정보"
    iso_formatter = getattr(now_kst, "isoformat", None)
    payload = {
        "query": normalized_query,
        "as_of_kst": iso_formatter() if callable(iso_formatter) else str(now_kst),
        "prior_context": prior_context[-1200:],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _format_realtime_lookup_context(evidence: str) -> str:
    """실시간 조회 스킬 stdout을 최종 답변용 system evidence 블록으로 감싼다."""
    return "\n".join(
        [
            _REALTIME_LOOKUP_CONTEXT_HEADER,
            "Use only the evidence below for live/current facts. "
            "Do not invent numbers, dates, sources, winners, prices, or news not present here. "
            "If the evidence says it is limited, say so explicitly.",
            # BIZ-383: 일정/상태성 질문은 timeline_validation 으로 출처의 시점 반영
            # 범위를 검증한다. status 를 그대로 신뢰해 stale 전망을 확정처럼 말하지 말 것.
            "If the evidence contains a `timeline_validation` object, respect its `status`: "
            "`stale_or_pre_event` means the source only describes a future/scheduled event — "
            "answer as a forecast, never as a confirmed result; "
            "`current_pending` means some events finished while others remain — separate the "
            "confirmed part from the pending part; "
            "`partial` means results may be only partially reflected — flag the partiality; "
            "`final` means a confirmed result — still state the as-of/source time; "
            "`no_evidence`/`unknown` means the timeline could not be verified — say so explicitly.",
            evidence.strip() or "{}",
        ]
    )


def _format_attachment_context_note(
    attachments: list[MultimodalAttachment] | None,
) -> str:
    """현재 turn 첨부 메타데이터와 분석 지시를 provider 입력용 note로 만든다."""
    if not attachments:
        return ""

    lines = [
        f"## {_ATTACHMENT_CONTEXT_HEADER}",
        "첨부 내용을 직접 분석해 주세요. 불가능하면 이유와 필요한 조치를 설명해 주세요.",
    ]
    for index, attachment in enumerate(attachments, start=1):
        name = attachment.name or f"attachment-{index}"
        size_bytes = attachment.size_bytes
        if size_bytes is None:
            size_bytes = len(attachment.data) if attachment.data is not None else None
        parts = [
            f"- Attachment {index}",
            f"File name: {name}",
            f"MIME: {attachment.mime_type}",
        ]
        if size_bytes is not None:
            parts.append(f"Size: {size_bytes} bytes")
        if attachment.path:
            parts.append(f"Sandbox path: {attachment.path}")
        lines.append("; ".join(parts))
    return "\n".join(lines)


def _tool_call_provides_live_evidence(tool_call: ToolCall) -> bool:
    """모델이 직접 요청한 도구 호출이 실시간 근거를 제공하는지 판정한다."""
    if tool_call.name == "web_fetch":
        return True
    if tool_call.name == "execute_skill":
        skill_name = str((tool_call.arguments or {}).get("skill_name", ""))
        return skill_name.lower() == _REALTIME_LOOKUP_SKILL_NAME
    return False


def _tool_result_looks_like_explicit_error(content: str) -> bool:
    """도구 결과가 명시적 오류 envelope/header 로 시작하는지 판정한다.

    정상 transcript/요약 본문에는 ``error``/``failed`` 같은 단어가 자연어로 섞일 수
    있다. 그래서 전체 본문 검색 대신 첫 non-empty line 또는 JSON-style envelope 처럼
    도구 실행 실패를 직접 선언하는 초반 헤더만 오류로 본다.
    """
    stripped = content.strip()
    if not stripped:
        return False

    lowered = stripped.lower()
    if lowered.startswith('{"error"') or lowered.startswith("{'error'"):
        return True

    for line in stripped.splitlines()[:3]:
        header = line.strip().lower()
        if not header:
            continue
        return any(
            header == prefix
            or header.startswith(f"{prefix}:")
            or header.startswith(f"{prefix} ")
            for prefix in _TOOL_RESULT_EMPTY_FINAL_ERROR_PREFIXES
        )
    return False

def _fallback_for_empty_final_after_tools(
    tool_results: list[tuple[str, str]],
) -> str:
    """도구 실행 후 LLM final 텍스트가 비었을 때 사용자 가시 fallback을 만든다.

    도구 결과까지 얻은 턴에서 “한 번 더 말해 달라”로 끝내면 이미 확인한
    사실(특히 빈 검색 결과)을 버리게 된다. 마지막 도구 결과를 보수적으로
    해석해, 빈 결과는 “못 찾음”, 오류 결과는 “확인 중 오류”로 분리한다.
    """
    if not tool_results:
        return _EMPTY_DIRECT_RESPONSE_MESSAGE

    name, content = tool_results[-1]
    stripped = content.strip()
    if not stripped:
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    lowered = stripped.lower()
    if _tool_result_looks_like_explicit_error(stripped):
        detail = stripped.splitlines()[0][:240]
        return _TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE.format(detail=detail)

    if any(
        marker in lowered or marker in stripped
        for marker in _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS
    ):
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    detail = stripped.replace("\n", " ")[:240]
    return _TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE.format(
        detail=f"{name}: {detail}",
    )



# BIZ-141 — forced final-answer LLM 호출이 provider 측에서 hang 하면 메시지가
# 영구 침묵하는 사고를 막기 위한 hard timeout. 일반 응답 시간(통상 1~3초) 대비
# 충분히 길고, hang 식별엔 충분히 짧은 경험적 컷.
_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS = 30.0
_FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE = (
    "응답이 지연되어 처리를 종료했습니다. 죄송하지만 한 번 더 말씀해 주세요. "
    "(debug: final-answer LLM 호출이 {timeout:.0f}초 안에 응답하지 않음)"
)

# BIZ-190 — ``agent-browser`` composite chain (``open && wait && text|evaluate``)
# 은 BIZ-187 에서 시스템 프롬프트 가드 + 180s 화이트리스트 타임아웃으로 봉합을
# 시도했지만, 작은 모델(gemini-2.5-flash-lite 등)이 가드 문구를 무시하고 첫 시도
# 부터 같은 chain 을 다시 보내는 패턴이 잔존(2026-05-13 20:19~20:36 KST 시드
# 측정 4건). 가드를 텍스트로만 두면 한 번 잘못된 시도를 못 막고 그 결과가 다시
# tool history 에 누적돼 후속 turn 까지 같은 chain 을 유도한다. 실행 직전에
# subprocess 전에서 차단하고 명확한 단일-호출 안내를 tool result 로 돌려 줌으로
# 써 LLM 이 같은 turn 안에서 정정하도록 한다.
_AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE = (
    "Error: composite `agent-browser` chains are blocked. Each agent-browser "
    "step must be a SEPARATE tool call (one `execute_skill` per `open`, `wait`, "
    "`get`/`evaluate` step). For plain page text, prefer `web_fetch` — it already "
    "auto-falls back to a headless browser. If `web_fetch` returned a short body "
    "for this URL, the site is blocking automated fetching; do NOT keep trying "
    "the same URL via agent-browser. Reply to the user that the page cannot be "
    "retrieved instead."
)

# BIZ-190 — 같은 URL 에 대해 ``agent-browser open`` 류 호출을 한 turn 안에서
# 반복하는 패턴(시드 측정 seed-2/3/8/9 의 4건 공통) 의 cap. 첫 시도가 daemon
# busy(os error 35) 등으로 실패하면 LLM 이 같은 명령을 재시도하면서 max-iter
# 까지 누적 소진한다. 첫 호출 1회만 허용하고 두 번째부터는 합성 응답으로
# 즉시 종결.
_AGENT_BROWSER_PER_TURN_CALL_CAP = 2

_AGENT_BROWSER_CAP_EXCEEDED_MESSAGE = (
    "Error: `agent-browser` has already been attempted {count} times in this "
    "turn and is being rate-limited to avoid exhausting the tool loop. If the "
    "page text could not be retrieved by `web_fetch` (which already includes a "
    "headless fallback), the site is blocking automated fetching. Reply to the "
    "user that the page cannot be retrieved rather than retrying with "
    "agent-browser, cli, or another skill."
)

# BIZ-251 — verifier footer 가 "변경 없음" 마커를 명시적으로 부착해야 하는
# tool 이름. 이들 도구는 디스크/외부 상태를 바꿀 *수* 있으므로, 호출 직후
# diff 가 비었다는 사실 자체가 LLM 의 silent-fail/환각 인지에 가치가 있다.
# read-only 도구(web_fetch, skill_docs, cron list, file_read) 는 빈 diff 가
# 정상 경로이므로 footer 를 생략해 토큰을 절약한다.
_FILE_MUTATING_TOOLS = frozenset(
    {"file_write", "file_manage", "execute_skill", "cli"}
)


class AgentOrchestrator:
    """페르소나 + 스킬 + 대화 이력 + LLM을 조합하는 중앙 오케스트레이터.

    응답 파이프라인 (Native Function Calling):
    1. 시스템 프롬프트 조립 (페르소나 + 스킬 개요 + 도구 사용 안내)
    2. 도구 정의를 LLM API의 tools 파라미터로 전달
    3. LLM이 tool_calls 반환 시 → 도구 실행 → 결과를 메시지에 추가 → 재호출
    4. LLM이 텍스트만 반환 시 → 최종 응답
    5. 대화 저장
    """

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        *,
        metrics: MetricsCollector | None = None,
        structured_logger: StructuredLogger | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        # 메트릭 수집기 — 서브프로세스 종료 결과를 누적하여 누수 추세를 모니터링.
        # None이면 메트릭이 기록되지 않으며, 기존 동작과 호환된다.
        self._metrics = metrics
        # 구조화 로거 — RAG 회상(action_type="rag_retrieve")과 같은 관찰 가능성 이벤트를 적재.
        # None이면 로그가 비활성화되며, 기존 동작과 호환된다.
        self._structured_logger = structured_logger

        # --- 정적 설정 로드 (리스타트 시에만 갱신) ---
        agent_config = load_agent_config(config_path)
        persona_config = load_persona_config(config_path)
        daemon_config = load_daemon_config(config_path)
        recipes_config = load_recipes_config(config_path)
        self._asset_selection_config = load_asset_selection_config(config_path)
        self._goal_loop_config = agent_config.get("goal_loop", {})
        self._complex_fact_config = agent_config.get("complex_fact_workflow", {})
        self._runtime_paths_prompt = self._format_runtime_paths_for_prompt(
            self._config_path,
            persona_config=persona_config,
            agent_config=agent_config,
            daemon_config=daemon_config,
            recipes_config=recipes_config,
        )

        # BIZ-202/BIZ-313: 봇이 채팅에서 만든 레시피와 데몬이 cron 으로 로드하는 레시피가
        # 같은 절대 경로를 보도록 config 한 곳에서 결정. 기본은
        # ``~/.simpleclaw-agent/default/recipes`` — 봇 워크스페이스
        # (`~/.simpleclaw-agent/default/workspace`) 의 sandbox-write 허용 트리 안에
        # 들어가야 봇 `cli`/`file_write` 도구가 직접 쓸 수 있다.
        self._recipes_dir = str(
            Path(recipes_config["dir"]).expanduser()
        )
        # 디렉터리는 부팅 시 자동 생성 — 없으면 봇이 새 레시피 작성을 시도하기 전에
        # mkdir 도구를 명령 받아야 하는 흐름이 되어 사용자 흐름이 깨진다.
        Path(self._recipes_dir).mkdir(parents=True, exist_ok=True)

        self._history_limit = agent_config["history_limit"]

        # 페르소나·스킬 설정값 보관 — _reload_dynamic_files()에서 참조
        self._persona_config = persona_config
        skills_config = self._load_skills_config()
        self._skills_config = skills_config
        self._skill_learning_config = load_skills_learning_config(config_path)

        # Cron scheduler — build_tool_definitions에서 참조하므로 리로드 전에 초기화
        self._cron_scheduler: CronScheduler | None = None

        # 초기 로드: 페르소나·스킬 파일을 디스크에서 읽어 캐시 필드 채움
        self._reload_dynamic_files()

        # LLM router
        self._router = create_router(config_path)

        # Conversation store
        # BIZ-313: db_path 가 ``~/.simpleclaw-agent/default/...`` 형태로 오므로 expanduser 로 풀어준다.
        db_path = Path(agent_config["db_path"]).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conversation_db_path = db_path
        self._store = ConversationStore(db_path)

        # 시맨틱 메모리(RAG, spec 005 Phase 2) 설정 로드
        # enabled=False가 기본 — sentence-transformers 미설치 환경에서도 무난하게 동작
        memory_config = load_memory_config(config_path)
        rag_cfg = memory_config["rag"]
        self._rag_enabled: bool = bool(rag_cfg["enabled"])
        self._rag_top_k: int = int(rag_cfg["top_k"])
        self._rag_threshold: float = float(rag_cfg["similarity_threshold"])
        long_term_cfg = memory_config.get("long_term", {})
        self._long_term_enabled: bool = bool(long_term_cfg.get("enabled", True))
        self._long_term_top_k: int = int(long_term_cfg.get("top_k", 3))
        self._long_term_min_confidence: float = float(
            long_term_cfg.get("min_confidence", 0.7)
        )
        self._long_term_promotion_threshold: int = int(
            long_term_cfg.get("promotion_threshold", 3)
        )
        self._long_term_context_budget_chars: int = int(
            long_term_cfg.get("context_budget_chars", 1600)
        )
        self._long_term_per_item_chars: int = int(
            long_term_cfg.get("per_item_chars", 400)
        )
        self._long_term_insights_file = Path(
            str(long_term_cfg.get("insights_file", "~/.simpleclaw-agent/default/insights.jsonl"))
        ).expanduser()
        self._long_term_active_projects_file = Path(
            str(long_term_cfg.get("active_projects_file", "~/.simpleclaw-agent/default/active_projects.jsonl"))
        ).expanduser()
        self._long_term_active_projects_window_days: int = int(
            long_term_cfg.get("active_projects_window_days", 7)
        )
        self._embedding_service: EmbeddingService | None = (
            EmbeddingService(
                model_name=str(rag_cfg["model"]),
                enabled=self._rag_enabled,
            )
            if self._rag_enabled
            else None
        )
        self._context_retrieval = ContextRetrievalService(
            store=self._store,
            embedding_service=self._embedding_service,
            structured_logger=self._structured_logger,
            config=ContextRetrievalConfig(
                rag_top_k=self._rag_top_k,
                rag_threshold=self._rag_threshold,
                long_term_enabled=self._long_term_enabled,
                long_term_top_k=self._long_term_top_k,
                long_term_min_confidence=self._long_term_min_confidence,
                long_term_promotion_threshold=self._long_term_promotion_threshold,
                long_term_context_budget_chars=self._long_term_context_budget_chars,
                long_term_per_item_chars=self._long_term_per_item_chars,
                long_term_insights_file=self._long_term_insights_file,
                long_term_active_projects_file=self._long_term_active_projects_file,
                long_term_active_projects_window_days=self._long_term_active_projects_window_days,
            ),
        )
        # 백그라운드 임베딩 태스크 강한 참조 — GC로 인한 task drop 방지
        self._background_tasks: set = set()

        # Skill execution timeout
        self._skill_timeout = skills_config.get("execution_timeout", 60)

        # Security: command guard + env filtering
        security_config = load_security_config(self._config_path)
        guard_config = security_config.get("command_guard", {})
        self._command_guard = CommandGuard(
            allowlist=guard_config.get("allowlist", []),
            enabled=guard_config.get("enabled", True),
        )
        self._env_passthrough = security_config.get("env_passthrough", [])
        _inject_env_secret_refs(security_config.get("env_secret_refs", {}))

        # Multi-turn tool execution budget
        self._max_tool_iterations = agent_config.get("max_tool_iterations", 15)

        # Workspace directory for skill file output.
        # BIZ-313: 기본 위치는 런타임 디렉터리(`~/.simpleclaw-agent/default/workspace`) — 저장소
        # working tree 안에 임시 파일이 쌓이지 않도록.
        self._workspace_dir = Path(
            agent_config.get("workspace_dir", "~/.simpleclaw-agent/default/workspace")
        ).expanduser()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        # BIZ-162: web_fetch 의 헤드리스 폴백이 nohup PATH 축소 환경에서도 동작하도록
        # 운영자 명시 경로를 config 에서 읽어 핸들러에 주입한다. None 이면 builtin_tools
        # 의 ``_resolve_agent_browser`` 가 PATH + 알려진 후보 경로 자동 탐색.
        web_fetch_cfg = agent_config.get("web_fetch", {}) or {}
        self._headless_binary: str | None = web_fetch_cfg.get("headless_binary")

        # BIZ-187: agent-browser composite chain (예: ``agent-browser open ... &&
        # agent-browser wait --load load && agent-browser text``) 은 SPA(wikidocs.net,
        # npmjs.com 등)에서 60s 의 기본 ``skills.execution_timeout`` 을 정기적으로
        # 넘어 ``Skill command timed out`` 으로 죽고, 모델이 tool loop 안에서 같은
        # composite 를 재시도하면서 ``max_tool_iterations`` 까지 누적 소진되는
        # 사고 다발(2026-05-13 BIZ-182 / BIZ-183 시드 측정). composite 한 호출의
        # 실제 wall time 은 보통 60~120s 이므로 ``agent-browser`` 명령에만 별도의
        # 더 긴 타임아웃을 화이트리스트로 적용한다. 기본 180s 는 시드 측정에서
        # 관찰된 최악(SPA 5건) 의 약 1.5배. None 으로 두면 기본 60s 유지.
        self._agent_browser_timeout: int = int(
            web_fetch_cfg.get("agent_browser_command_timeout", 180)
        )

        # BIZ-251: per-turn file mutation verifier footer.
        # 워크스페이스는 재귀 walk, 페르소나 dir 은 명시 파일 화이트리스트
        # (AGENT.md / USER.md / MEMORY.md) 만 추적해 SQLite/dreaming 부산물이
        # footer 노이즈로 새는 것을 차단한다. ``~/.simpleclaw-agent/default`` 가 persona
        # local_dir 인 BIZ-313 경로 가정 — 화이트리스트면 overlap 도 안전.
        persona_local = Path(
            self._persona_config["local_dir"]
        ).expanduser()
        persona_filenames = tuple(
            f["name"] for f in self._persona_config["files"] if "name" in f
        )
        self._mutation_tracker = FileMutationTracker(
            [
                TrackedRoot(".agent/workspace", self._workspace_dir),
                TrackedRoot(".agent", persona_local, files=persona_filenames),
            ]
        )

        # BIZ-260 — clarify 도구의 pending 요청 레지스트리. chat_id → ClarifyRequest.
        # ``_dispatch_tool_call`` 이 채워 넣고, 채널이 ``pop_pending_clarify`` 로 회수.
        # 동일 chat 안에서는 한 번에 하나만 대기 — 새 clarify 가 호출되면 덮어쓴다.
        self._pending_clarify: dict[int, ClarifyRequest] = {}

        proactive_config = daemon_config.get("proactive", {}) or {}
        conversation_config = (
            proactive_config.get("extractors", {}).get("conversation_end", {})
            if isinstance(proactive_config.get("extractors", {}), dict)
            else {}
        )
        self._conversation_end_detector = ConversationEndDetector(
            store=OpportunityStore(proactive_config.get("store_file", "~/.simpleclaw-agent/default/proactive_opportunities.jsonl")),
            enabled=bool(proactive_config.get("enabled", False))
            and bool(conversation_config.get("enabled", False)),
            max_latency_ms=int(conversation_config.get("max_latency_ms", 50) or 50),
        )

        logger.info(
            "AgentOrchestrator initialized: persona=%d chars, skills=%d, backend=%s",
            len(self._persona_prompt),
            len(self._skills),
            self._router.get_default_backend(),
        )

    def _reload_dynamic_files(self) -> None:
        """페르소나·스킬 파일을 디스크에서 다시 읽어 캐시 필드를 갱신한다 (hot-reload).

        호출 시점: __init__() 초기화 + 매 메시지 진입 시 1회.
        tool loop 내부에서는 호출하지 않아 불필요한 I/O를 방지한다.
        """
        # --- 페르소나 리로드 (AGENT.md, USER.md, MEMORY.md) ---
        persona_files = resolve_persona_files(
            local_dir=self._persona_config["local_dir"],
            global_dir=self._persona_config["global_dir"],
        )
        assembly = assemble_prompt(
            persona_files, self._persona_config["token_budget"]
        )
        self._persona_prompt = assembly.assembled_text or ""

        # --- 스킬 리로드 (.agent/skills, ~/.agents/skills) ---
        self._skills = discover_skills(
            local_dir=self._skills_config.get("local_dir", ".agent/skills"),
            global_dir=self._skills_config.get("global_dir", "~/.agents/skills"),
        )
        # 이름 기반 조회용 딕셔너리 (fuzzy match에서도 사용)
        # BIZ-383: realtime-lookup-skill 은 오케스트레이터가 LLM 루프 밖에서 직접
        # 실행하는 내부 evidence 스킬이다. _resolve_skill_name 으로 내부 실행은 가능해야
        # 하므로 by-name 매핑에는 남기되, LLM callable 목록/프롬프트에서는 제외한다.
        self._skills_by_name = {s.name: s for s in self._skills}
        # 시스템 프롬프트용 스킬 목록 (내부 evidence 스킬 제외)
        self._skills_prompt = self._format_skills_for_prompt(self._exposable_skills())

        # --- 레시피 리로드 (~/.simpleclaw-agent/default/recipes) ---
        # selector manifest와 선택 레시피 컨텍스트가 운영 recipe 디렉터리 변경을
        # 재시작 없이 반영하도록 매 메시지 진입 시 스캔한다. 실패는 selector 보조
        # 경로만 비우고 main 응답은 기존 스킬 경로로 계속 진행한다.
        try:
            self._recipes = discover_recipes(self._recipes_dir)
        except Exception as exc:  # noqa: BLE001 — recipe 스캔 실패는 응답을 막지 않음
            logger.warning("Recipe discovery failed during dynamic reload: %s", exc)
            self._recipes = []

    def set_cron_scheduler(self, scheduler: CronScheduler) -> None:
        """CronScheduler를 주입하여 cron 도구를 활성화한다."""
        self._cron_scheduler = scheduler
        logger.info("CronScheduler injected into AgentOrchestrator.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_cron_message(self, text: str) -> str:
        """크론 잡 메시지를 격리된 컨텍스트로 처리한다.

        대화 이력을 불러오지 않고 공유 대화 DB에 메시지를 저장하지 않는다.
        진입점이므로 trace_id를 새로 발급해 호출 체인 전체로 전파한다.
        """
        with trace_scope() as trace_id:
            logger.info("Cron message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            return await self._tool_loop(
                text,
                isolated=True,
                allow_cron_mutation=False,
            )

    async def process_message(
        self,
        text: str,
        user_id: int,
        chat_id: int,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
    ) -> str:
        """수신 메시지를 Native Function Calling 파이프라인으로 처리한다.

        진입점이므로 trace_id를 새로 발급해 ``contextvars``로 호출 체인
        (도구 실행, RAG 회상, 백그라운드 임베딩, 서브에이전트/스킬 등) 전체에
        전파한다. ``trace_scope``는 ``with`` 블록 종료 시 이전 trace_id를
        복원하므로 동일 프로세스에서 후속 메시지가 깨끗한 컨텍스트로 시작된다.

        BIZ-259: ``on_text_delta`` 콜백이 주어지면 ``_tool_loop`` 가 LLM 응답 텍스트
        델타를 콜백으로 흘려보낸다. ``/cron``, ``/recipe-*`` 명령어 분기는 즉답 분기
        이므로 콜백을 무시한다 — 부분 결과로 알림 트리거되는 사고 방지(``final_only``).
        """
        with trace_scope() as trace_id:
            logger.info(
                "Message received: trace_id=%s user=%d chat=%d",
                trace_id, user_id, chat_id,
            )
            self._reload_dynamic_files()

            undo_command, undo_turns = _parse_undo_command(text)
            if undo_command:
                if undo_turns is None:
                    return _UNDO_USAGE_MESSAGE
                hidden_turns = self._store.hide_recent_user_turns(undo_turns)
                if hidden_turns == 0:
                    return _UNDO_NO_TURNS_MESSAGE
                return _UNDO_SUCCESS_MESSAGE.format(turns=hidden_turns)

            # BIZ-260 — clarify 도구가 발생시킬 ClarifyRequest 를 chat_id 키로
            # 적재할 수 있도록 contextvar 에 chat_id 를 매단다. tool 핸들러는
            # 자기 시그니처를 바꾸지 않고도 contextvar 로 chat_id 를 얻는다.
            clarify_token = clarify_chat_id_var.set(chat_id)
            try:
                # /goal 명령어 확인 — recipe dispatch 보다 먼저 처리해 `/goal` 레시피 오인 방지.
                goal_command = parse_goal_command(text)
                if goal_command is not None:
                    if goal_command.action in {"help", "unsupported"}:
                        self._save_turn(
                            text,
                            goal_command.message,
                            channel=f"{CHANNEL_GOAL_PREFIX}admin",
                        )
                        return goal_command.message
                    if not self._goal_loop_config.get("enabled", True):
                        disabled = "⚠️ /goal 기능이 현재 비활성화되어 있습니다."
                        self._save_turn(
                            text, disabled, channel=f"{CHANNEL_GOAL_PREFIX}disabled"
                        )
                        return disabled

                    cfg = GoalLoopConfig(
                        max_rounds=int(self._goal_loop_config.get("max_rounds", 3)),
                        judge_max_tokens=int(
                            self._goal_loop_config.get("judge_max_tokens", 768)
                        ),
                        max_answer_chars_for_judge=int(
                            self._goal_loop_config.get(
                                "max_answer_chars_for_judge", 6000
                            )
                        ),
                    )

                    async def run_goal_round(prompt: str, **kwargs):
                        kwargs.pop("allow_cron_mutation", None)
                        kwargs["on_text_delta"] = None
                        return await self._run_tool_loop_result(
                            prompt,
                            isolated=False,
                            attachments=attachments,
                            operator_tools=operator_tools,
                            allow_cron_mutation=False,
                            **kwargs,
                        )

                    runner = GoalLoopRunner(
                        run_round=run_goal_round,
                        judge_send=self._router.send,
                        config=cfg,
                    )
                    goal_result = await runner.run(
                        goal_command.objective,
                        on_progress=on_progress,
                    )
                    self._save_turn(
                        text,
                        goal_result.final_text,
                        channel=f"{CHANNEL_GOAL_PREFIX}{goal_result.status}",
                    )
                    return goal_result.final_text

                # /cron 명령어 확인
                cron_result = try_cron_command(text, self._cron_scheduler)
                if cron_result is not None:
                    # BIZ-76 — cron 관리 명령(/cron list 등) 응답은 자동 트리거
                    # 카테고리로 묶어 dreaming 의 사용자 관심 추론에서 분리한다.
                    self._save_turn(
                        text, cron_result, channel=CHANNEL_CRON_ADMIN,
                    )
                    return cron_result

                # /recipe-name 명령어 확인 (e.g. /ai-report)
                # BIZ-202: 레시피 디렉터리는 config 기반 — 봇/데몬 양쪽이 같은 절대 경로를 본다.
                recipe_outcome = await try_recipe_command(
                    text,
                    self._tool_loop,
                    recipes_dir=self._recipes_dir,
                    on_progress=on_progress,
                )
                if recipe_outcome is not None:
                    recipe_result, recipe_name = recipe_outcome
                    # BIZ-76 — 레시피 산출물은 사용자 발화가 아니라 자동/명령 트리거
                    # 결과이므로 ``recipe:<name>`` 채널로 태깅한다. dreaming 코퍼스
                    # 로더가 이 prefix 를 보고 분리 또는 가중치 다운한다.
                    self._save_turn(
                        text,
                        recipe_result,
                        channel=f"{CHANNEL_RECIPE_PREFIX}{recipe_name}",
                    )
                    return recipe_result

                route_decision = classify_response_route(
                    text,
                    route_threshold=int(
                        self._complex_fact_config.get("route_threshold", 3)
                    ),
                )
                if (
                    self._complex_fact_config.get("enabled", False)
                    and route_decision.route == ResponseRoute.COMPLEX_FACT_WORKFLOW
                ):
                    response_text = await self._run_complex_fact_workflow(
                        text,
                        route_decision,
                        on_progress=on_progress,
                    )
                    tool_loop_result = ToolLoopResult(response_text)
                elif self._skill_learning_config.get("enabled", False):
                    tool_loop_result = await self._run_tool_loop_result(
                        text,
                        attachments=attachments,
                        on_text_delta=on_text_delta,
                        on_progress=on_progress,
                    )
                    response_text = tool_loop_result.text
                else:
                    response_text = await self._tool_loop(
                        text,
                        attachments=attachments,
                        on_text_delta=on_text_delta,
                        on_progress=on_progress,
                    )
                    tool_loop_result = ToolLoopResult(response_text)

                # BIZ-260 — clarify 가 호출됐다면 ``_tool_loop`` 가 빈 텍스트로
                # 종결했을 수 있다. 대화 이력 저장은 항상 "질문 + 번호 옵션"
                # 텍스트로 — 다음 turn 의 LLM 컨텍스트에 옵션이 보존되어 사용자가
                # 텍스트로 "1" / 본문으로 답해도 매칭 가능 (DoD backward compat).
                pending = self._pending_clarify.get(chat_id)
                if pending is not None:
                    response_text = pending.format_user_visible()

                # 일반 사용자 발화는 채널을 명시하지 않는다(=organic). 이후 BIZ-76
                # 후속에서 telegram/webhook/console 같은 origin 메타로 확장될 수 있음.
                msg_ids = self._save_turn(text, response_text)
                await self._capture_conversation_end_opportunity(
                    text, response_text, list(msg_ids)
                )
                await self._capture_skill_learning_candidate(
                    text, response_text, tool_loop_result, list(msg_ids)
                )
                return response_text
            finally:
                clarify_chat_id_var.reset(clarify_token)

    async def process_operator_message(
        self,
        text: str,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> str:
        """운영자 context에서만 operator scope native tool을 노출해 메시지를 처리한다."""
        with trace_scope() as trace_id:
            logger.info("Operator message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            return await self._tool_loop(
                text,
                isolated=True,
                on_text_delta=on_text_delta,
                operator_tools=True,
            )

    async def _run_complex_fact_workflow(
        self,
        text: str,
        route_decision,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        """Feature-flagged complex fact/scenario workflow entrypoint."""
        from simpleclaw.agent.evidence_retrieval import EvidenceRetriever
        from simpleclaw.agent.fact_answer import compose_fact_answer
        from simpleclaw.agent.fact_workflow import (
            ComplexFactWorkflow,
            ComplexFactWorkflowConfig,
        )

        cfg = self._complex_fact_config or {}
        if str(cfg.get("planner_backend", "simpleclaw")) != "simpleclaw":
            logger.warning(
                "Complex fact planner backend %s is not implemented; falling back to simpleclaw",
                cfg.get("planner_backend"),
            )
        retriever = EvidenceRetriever(
            max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3))
        )

        async def compose(question, plan):
            return await compose_fact_answer(self._router.send, question, plan)

        workflow = ComplexFactWorkflow(
            retriever=retriever,
            compose_answer=compose,
            config=ComplexFactWorkflowConfig(
                max_iterations=int(cfg.get("max_iterations", 4)),
                max_sources_per_slot=int(cfg.get("max_sources_per_slot", 3)),
                enable_claim_verifier=bool(cfg.get("enable_claim_verifier", True)),
                enable_progress_events=bool(cfg.get("enable_progress_events", True)),
            ),
        )
        result = await workflow.run(text, route_decision, on_progress=on_progress)
        return result.text

    # ------------------------------------------------------------------
    # 대화 저장 + 백그라운드 임베딩 (spec 005 Phase 2)
    # ------------------------------------------------------------------

    def _save_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        channel: str | None = None,
    ) -> tuple[int, int]:
        """user/assistant 메시지 한 쌍을 저장하고, RAG가 켜져 있으면 임베딩을 백그라운드 부착한다.

        설계 결정:
        - 임베딩은 fire-and-forget 비동기로 처리하여 응답 레이턴시에 영향을 주지 않는다.
        - 동일 턴 내 user → assistant 순서로 저장(시간순 보존).
        - RAG가 비활성이거나 임베딩 서비스가 None이면 저장만 수행한다.

        BIZ-76: ``channel`` 인자가 주어지면 같은 턴의 user/assistant 두 메시지 모두에
        동일 채널을 부착한다. cron-admin / recipe:<name> 같은 자동·명령 트리거 출처를
        이후 dreaming 코퍼스 로더가 분리하거나 가중치 다운하기 위한 메타이다.
        """
        user_id = self._store.add_message(ConversationMessage(
            role=MessageRole.USER, content=user_text, channel=channel,
        ))
        asst_id = self._store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content=assistant_text, channel=channel,
        ))
        self._schedule_embedding(user_id, user_text)
        self._schedule_embedding(asst_id, assistant_text)
        return user_id, asst_id

    async def _capture_conversation_end_opportunity(
        self, user_text: str, assistant_text: str, source_msg_ids: list[int]
    ) -> None:
        """대화 종료 hook을 best-effort로 실행해 pending 후보만 적재한다."""
        detector = getattr(self, "_conversation_end_detector", None)
        if detector is None:
            return
        try:
            detector.capture(
                user_text=user_text,
                assistant_text=assistant_text,
                source_msg_ids=source_msg_ids,
            )
        except Exception:  # noqa: BLE001 — proactive 후보 실패가 사용자 응답을 깨면 안 된다.
            logger.exception("Conversation-end proactive hook failed")

    async def _capture_skill_learning_candidate(
        self,
        user_text: str,
        assistant_text: str,
        result: ToolLoopResult,
        source_msg_ids: list[int],
    ) -> None:
        """성공한 복잡 tool trace를 best-effort pending skill 후보로 적재한다."""
        cfg = getattr(self, "_skill_learning_config", {}) or {}
        if not cfg.get("enabled", False):
            return
        try:
            trace = list(result.trace or [])
            if not result.success or not is_complex_successful_trace(
                trace,
                assistant_text,
                min_tool_calls=int(cfg.get("min_tool_calls", 2)),
                min_distinct_tools=int(cfg.get("min_distinct_tools", 2)),
                min_final_chars=int(cfg.get("min_final_chars", 500)),
            ):
                return
            snapshots = snapshots_from_trace(
                trace,
                max_observation_chars=int(cfg.get("max_trace_observation_chars", 1200)),
            )
            suggestion = await self._draft_skill_suggestion(
                user_text=user_text,
                assistant_text=assistant_text,
                snapshots=snapshots,
                source_msg_ids=source_msg_ids,
            )
            SkillSuggestionStore(cfg["suggestions_file"]).upsert_pending(suggestion)
        except Exception:  # noqa: BLE001 — 학습 후보 실패가 사용자 응답을 깨면 안 된다.
            logger.exception("Skill-learning candidate hook failed")

    async def _draft_skill_suggestion(
        self,
        *,
        user_text: str,
        assistant_text: str,
        snapshots: list,
        source_msg_ids: list[int],
    ) -> SkillSuggestion:
        """LLM으로 skill package 후보 JSON을 생성한다."""
        fp = trace_fingerprint(snapshots, user_text=user_text, assistant_text=assistant_text)
        prompt = build_skill_candidate_prompt(
            user_text=user_text,
            assistant_text=assistant_text,
            trace=snapshots,
        )
        response = await self._router.send(LLMRequest(user_message=prompt, max_tokens=2048))
        payload = json.loads((response.text or "{}").strip())
        if not isinstance(payload, dict):
            raise ValueError("Skill candidate response must be a JSON object")
        return suggestion_from_candidate_payload(
            payload,
            trace_fingerprint_value=fp,
            source_msg_ids=source_msg_ids,
            trace=snapshots,
        )

    def _schedule_embedding(self, message_id: int, content: str) -> None:
        """주어진 메시지의 임베딩을 백그라운드 태스크로 부착한다.

        실패는 조용히 로그만 남긴다(메시지 자체 저장은 이미 완료되었으므로 RAG만 누락).
        sentence-transformers 모델은 동기 API라 ``asyncio.to_thread``로 워커 스레드에 위임한다.
        """
        if self._embedding_service is None or not self._embedding_service.is_enabled:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 호출 컨텍스트에 이벤트 루프가 없으면 임베딩을 건너뛴다(테스트/동기 호출 보호)
            return

        task = loop.create_task(self._embed_message_async(message_id, content))
        # 강한 참조 유지 — 완료되면 set에서 제거
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _embed_message_async(self, message_id: int, content: str) -> None:
        """메시지를 임베딩하고 ConversationStore에 부착한다(워커 스레드 위임).

        모든 단계는 best-effort이며, 어떤 실패도 호출자로 전파되지 않는다.
        """
        import asyncio
        try:
            assert self._embedding_service is not None  # 호출 전에 확인됨
            vec = await asyncio.to_thread(
                self._embedding_service.encode_passage, content
            )
            if vec is None:
                return
            await asyncio.to_thread(self._store.add_embedding, message_id, vec)
        except Exception as exc:
            logger.warning(
                "Background embedding failed for msg %d: %s", message_id, exc
            )

    # ------------------------------------------------------------------
    # Native Function Calling loop
    # ------------------------------------------------------------------

    async def _prepare_tool_loop_state(
        self,
        text: str,
        isolated: bool,
        *,
        attachments: list[MultimodalAttachment] | None,
        on_text_delta: TextDeltaCallback | None,
        on_progress: ProgressCallback | None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
    ) -> ToolLoopState:
        """tool loop runner 입력 상태를 조립한다.

        컨텍스트/RAG/자산 선택/실시간 evidence 준비는 오케스트레이터 경계에 남기고,
        실제 반복 lifecycle은 ``ToolLoopRunner``가 담당하도록 상태 객체만 만든다.
        """
        # 현재 시각을 KST로 주입
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        datetime_context = now_kst.strftime(
            "[현재 시각: %Y-%m-%d %H:%M (%A) KST]"
        )
        user_content = f"{datetime_context}\n{text}"
        attachment_note = _format_attachment_context_note(attachments)
        if attachment_note:
            user_content = f"{user_content}\n\n{attachment_note}"
        current_user_message: dict = {"role": "user", "content": user_content}
        if attachments:
            # 이미지 bytes는 현재 turn에만 첨부한다. 영속 대화 저장소에는 대용량
            # 바이너리를 넣지 않고 텍스트 발화만 저장해 RAG/히스토리 오염을 피한다.
            current_user_message["attachments"] = attachments

        # 메시지 구성
        if isolated:
            messages: list[dict] = [current_user_message]
            prior_context = ""
            rag_context = ""
        else:
            recent = self._store.get_recent(limit=self._history_limit)
            # BIZ-164 — 과거 턴의 ``role=tool`` 메시지와 assistant 메시지의
            # ``tool_calls`` 필드는 다음 턴의 LLM 입력에서 잘라낸다. 5/10 의
            # ``link-git-summarizer`` 같은 실패 도구 호출이 history 에 남아
            # 있으면 작은 모델이 새 사용자 메시지에서도 같은 도구를 다시
            # 시도해 max-iter 까지 낭비하는 사고(2026-05-12 17:46)가 잡힌다.
            # 현재 ``MessageRole`` 은 user/assistant/system 만 정의하므로 실데이터
            # 에선 no-op 이지만, 향후 store 가 tool 역할을 적재하거나 메시지에
            # ``tool_calls`` 속성이 부착되더라도 누설되지 않도록 명시적으로 거른다.
            # 현재 턴 내부(in-flight)의 tool exchange 는 아래 루프에서 그대로
            # 누적되므로 정보 손실 없음.
            messages = []
            prior_context_parts: list[str] = []
            for msg in recent:
                role_value = msg.role.value
                if role_value not in ("user", "assistant", "system"):
                    continue
                prior_context_parts.append(msg.content)
                messages.append({
                    "role": role_value,
                    "content": msg.content,
                })
            prior_context = "\n".join(prior_context_parts[-6:])
            messages.append(current_user_message)
            # 시맨틱 회상: 최근 윈도우에 포함되지 않은 과거 메시지를 추가 컨텍스트로 회수
            recent_contents = {msg.content for msg in recent}
            rag_context = await self._retrieve_relevant_context(
                text, exclude_contents=recent_contents,
            )

        # BIZ-383: 내부 evidence 스킬을 LLM callable 후보(asset 선택/프롬프트)에서 제외.
        active_skills = self._exposable_skills()
        active_recipes = getattr(self, "_recipes", [])
        active_skills_prompt = self._skills_prompt
        active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)
        active_recipes_before_skills = False

        asset_selection = await self._select_assets_for_turn(text, active_skills, active_recipes)
        if asset_selection is not None and not asset_selection.fallback_required:
            selected_skills, selected_recipes = filter_assets_by_selection(
                skills=active_skills,
                recipes=active_recipes,
                selection=asset_selection,
                skill_top_k=int(self._asset_selection_config["skill_top_k"]),
                recipe_top_k=int(self._asset_selection_config["recipe_top_k"]),
            )
            if selected_skills or selected_recipes:
                active_skills = selected_skills
                active_recipes = selected_recipes
                active_skills_prompt = self._format_skills_for_prompt(selected_skills)
                active_recipes_prompt = self._format_recipes_for_prompt(selected_recipes)
                active_recipes_before_skills = bool(selected_recipes)
        elif asset_selection is not None and asset_selection.fallback_required:
            fallback_top_k = int(self._asset_selection_config.get("fallback_top_k", 0))
            if fallback_top_k > 0:
                active_skills = active_skills[:fallback_top_k]
                active_recipes = active_recipes[:fallback_top_k]
                active_skills_prompt = self._format_skills_for_prompt(active_skills)
                active_recipes_prompt = self._format_recipes_for_prompt(active_recipes)

        realtime_lookup_context = ""
        realtime_lookup_payload = _realtime_lookup_skill_payload(
            text,
            now_kst,
            prior_context=prior_context,
        )
        if (
            realtime_lookup_payload is not None
            and self._resolve_skill_name(_REALTIME_LOOKUP_SKILL_NAME) is not None
        ):
            try:
                realtime_lookup_result = await self._execute_skill(
                    _REALTIME_LOOKUP_SKILL_NAME,
                    realtime_lookup_payload,
                )
                realtime_lookup_context = _format_realtime_lookup_context(
                    sanitize_tool_output(realtime_lookup_result or ""),
                )
                logger.info(
                    "BIZ-359: realtime lookup skill evidence injected (%d chars)",
                    len(realtime_lookup_context),
                )
            except Exception as exc:  # noqa: BLE001 — evidence 조회 실패가 turn 전체를 죽이지 않음
                realtime_lookup_context = _format_realtime_lookup_context(
                    json.dumps(
                        {
                            "kind": "realtime_lookup",
                            "confidence": "low",
                            "facts": [],
                            "limitations": [
                                f"realtime-lookup-skill failed: {str(exc)[:200]}"
                            ],
                        },
                        ensure_ascii=False,
                    ),
                )
                logger.warning("BIZ-359: realtime lookup skill failed: %s", exc)
        if realtime_lookup_context:
            rag_context = "\n\n".join(part for part in [rag_context, realtime_lookup_context] if part)

        # 시스템 프롬프트는 페르소나/스킬과 RAG 회상 블록을 합친 결과.
        # BIZ-252 — Claude 의 prompt caching 을 위해 세그먼트 단위로도 함께 보낸다.
        # cache 경계: 페르소나 끝 / 스킬 목록 끝. ReAct 지시문과 RAG 블록은 마커 뒤에 둔다.
        system_blocks = self._build_system_blocks(
            rag_context=rag_context,
            skills_prompt=active_skills_prompt,
            recipes_prompt=active_recipes_prompt,
            recipes_before_skills=active_recipes_before_skills,
        )
        system_prompt = self._flatten_system_blocks(system_blocks)
        scopes = (
            (ToolScope.RUNTIME, ToolScope.OPERATOR, ToolScope.DEVELOPMENT)
            if operator_tools
            else (ToolScope.RUNTIME,)
        )
        tools = build_tool_definitions(
            active_skills,
            cron_available=self._cron_scheduler is not None,
            scopes=scopes,
            operator_gate=operator_tools,
        )
        return ToolLoopState(
            user_content=user_content,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            system_blocks=system_blocks,
            previous_mutation_snapshot=self._mutation_tracker.snapshot(),
            on_text_delta=on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
        )

    async def _run_tool_loop_result(
        self,
        text: str,
        isolated: bool = False,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
    ) -> ToolLoopResult:
        """Native Function Calling 루프를 실행하고 structured result를 반환한다.

        LLM에 도구 정의(tools)와 함께 메시지를 전송하고,
        tool_calls가 반환되면 실행 후 결과를 메시지에 추가하여 재호출한다.
        텍스트만 반환되면 최종 응답으로 반환한다.

        Args:
            text: 사용자 원본 메시지
            isolated: True면 대화 이력 없이 독립 실행 (크론 잡 등). 크론 분기는
                ``final_only_for_cron`` 정책에 따라 호출 측에서 ``on_text_delta`` 를
                None 으로 넘긴다 — 본 함수는 콜백 유무로만 동작 분기.
            on_text_delta: BIZ-259 — 텍스트 델타 콜백. 주어지면 라우터의 ``stream()``
                경로로 전환되어 각 iteration 의 텍스트 델타가 콜백으로 흐른다.
                tool-call iteration 의 ReAct thought 텍스트도 그대로 흐르므로 sink
                측에서 finalize 시 최종 텍스트로 덮어쓰는 패턴을 따른다.
        """
        state = await self._prepare_tool_loop_state(
            text,
            isolated,
            attachments=attachments,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
        )
        result = await ToolLoopRunner(self).run(state)
        return result

    async def _tool_loop(
        self,
        text: str,
        isolated: bool = False,
        *,
        attachments: list[MultimodalAttachment] | None = None,
        on_text_delta: TextDeltaCallback | None = None,
        on_progress: ProgressCallback | None = None,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
    ) -> str:
        """기존 호출자를 위한 문자열 compatibility wrapper."""
        result = await self._run_tool_loop_result(
            text,
            isolated,
            attachments=attachments,
            on_text_delta=on_text_delta,
            on_progress=on_progress,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
        )
        return result.text

    # ------------------------------------------------------------------
    # Asset selector
    # ------------------------------------------------------------------

    async def _select_assets_for_turn(
        self,
        text: str,
        skills: list[SkillDefinition],
        recipes: list[RecipeDefinition],
    ) -> AssetSelectionResult | None:
        """설정이 켜진 경우 selector LLM으로 이번 turn의 자산 후보를 축소한다.

        selector는 사용자 응답 경로를 막지 않는 best-effort 보조 호출이다. 설정이
        꺼져 있거나 후보군이 작거나 호출/정규화가 실패하면 None을 반환해 기존 전체
        후보 프롬프트와 도구 스키마를 그대로 사용한다.
        """
        cfg = self._asset_selection_config
        if not cfg.get("enabled", False):
            return None
        known_assets = build_selector_assets(skills, recipes)
        if not known_assets:
            return None
        if len(known_assets) <= int(cfg.get("bypass_below_count", 0)):
            logger.info(
                "Asset selector bypassed: candidates=%d threshold=%d",
                len(known_assets),
                int(cfg.get("bypass_below_count", 0)),
            )
            return None

        prompt = build_selector_prompt(
            user_message=text,
            known_assets=known_assets,
            skill_top_k=int(cfg["skill_top_k"]),
            recipe_top_k=int(cfg["recipe_top_k"]),
        )
        try:
            response = await self._router.send(
                LLMRequest(
                    system_prompt=load_system_prompt("asset_selector").system_prompt,
                    user_message=prompt,
                    backend_name=str(cfg["backend"]),
                    tools=[build_selector_tool_definition()],
                    max_tokens=int(cfg["max_tokens"]),
                )
            )
            result = normalize_selector_response(
                user_message=text,
                known_assets=known_assets,
                response_text=response.text or "",
                tool_calls=response.tool_calls,
                top_k=int(cfg["skill_top_k"]) + int(cfg["recipe_top_k"]),
                min_confidence=float(cfg["min_confidence"]),
            )
        except Exception as exc:  # noqa: BLE001 — selector 실패는 main 응답을 막지 않음
            logger.warning("Asset selector failed; falling back to capped assets: %s", exc)
            return AssetSelectionResult(
                fallback_required=True,
                fallback_reason="selector_error",
            )

        if result.fallback_required:
            logger.info(
                "Asset selector fallback: reason=%s selected=%d",
                result.fallback_reason,
                len(result.selected),
            )
        else:
            logger.info("Asset selector selected %d candidate(s)", len(result.selected))
        return result

    # ------------------------------------------------------------------
    # Active Memory tool
    # ------------------------------------------------------------------

    async def _search_memory(self, args: dict) -> str:
        """Active Memory 도구 dispatch 를 전용 모듈에 위임한다."""
        return await memory_search.search_memory(self, args)

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(
        self,
        tool_call: ToolCall,
        *,
        operator_tools: bool = False,
        allow_cron_mutation: bool = True,
    ) -> str:
        """ToolCall 라우팅을 전용 모듈에 위임한다."""
        return await tool_dispatch.dispatch_tool_call(
            self,
            tool_call,
            operator_tools=operator_tools,
            allow_cron_mutation=allow_cron_mutation,
        )


    @staticmethod
    def _progress_identity_for_tool_call(tool_call: ToolCall) -> tuple[str, str]:
        """ToolCall 을 사용자 표시용 progress 종류/이름으로 축약한다."""
        name = tool_call.name
        args = tool_call.arguments or {}
        if name == "cli":
            return "command", "cli"
        if name == "execute_skill":
            skill_name = str(args.get("skill_name") or "execute_skill")
            if args.get("command"):
                return "command", skill_name
            return "skill", skill_name
        return "tool", name

    def pop_pending_clarify(self, chat_id: int) -> ClarifyRequest | None:
        """채널이 ``process_message`` 후 호출 — pending clarify 를 회수·제거한다.

        BIZ-260: 한 chat 의 다음 메시지가 도착하기 전까지 ``_pending_clarify[chat_id]``
        에 머무르지만, 채널이 인라인 키보드 렌더에 성공하면 즉시 제거해 다음 호출이
        깨끗한 상태에서 시작되도록 한다. 인라인 키보드를 지원하지 않는 채널
        (webhook 등) 은 이 메서드를 호출하지 않고 ``format_user_visible`` 텍스트를
        그대로 사용자에게 노출한다.
        """
        return self._pending_clarify.pop(chat_id, None)

    async def _dispatch_external_skill(self, args: dict) -> str:
        """execute_skill 도구 dispatch 를 전용 모듈에 위임한다."""
        return await skill_dispatch.dispatch_external_skill(self, args)

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    # BIZ-252 — Anthropic prompt caching 경계.
    # 시스템 프롬프트를 (persona, skills, rag, react) 세그먼트로 쪼개되,
    # 각 세그먼트의 trailing separator 를 텍스트에 포함시켜 단순 합치기(``"".join``)가
    # 기존 ``"\n\n---\n\n".join(parts)`` 와 byte-identical 한 결과를 내도록 한다.
    # 이 덕분에 Claude 가 content blocks 리스트로 받아도, 비-Claude 프로바이더가
    # 평탄화 문자열로 받아도 동일한 prefix 가 노출된다.
    _SYSTEM_BLOCK_SEPARATOR = "\n\n---\n\n"

    @staticmethod
    def _format_runtime_paths_for_prompt(
        config_path: Path,
        *,
        persona_config: dict,
        agent_config: dict,
        daemon_config: dict,
        recipes_config: dict,
    ) -> str:
        """live 배포 repo와 런타임 state 경로를 시스템 프롬프트용으로 요약한다.

        BIZ-313: 모델이 ``~/.simpleclaw``(배포 repo/config)와
        ``~/.simpleclaw-agent/default``(대화 DB·레시피·workspace·페르소나 파일)를
        혼동하면 잘못된 파일을 읽거나 새 레시피를 레거시 위치에 쓰게 된다. 이 블록은
        config 로더가 실제로 반환한 경로만 노출해 운영 설정과 프롬프트를 맞춘다.
        """
        deploy_repo = config_path.expanduser().resolve().parent
        persona_dir = Path(str(persona_config["local_dir"])).expanduser()
        workspace_dir = Path(str(agent_config["workspace_dir"])).expanduser()
        recipes_dir = Path(str(recipes_config["dir"])).expanduser()
        conversation_db = Path(str(agent_config["db_path"])).expanduser()
        daemon_db = Path(str(daemon_config["db_path"])).expanduser()
        return load_system_prompt("runtime_paths").format_field(
            deploy_repo=deploy_repo,
            persona_dir=persona_dir,
            conversation_db=conversation_db,
            daemon_db=daemon_db,
            recipes_dir=recipes_dir,
            workspace_dir=workspace_dir,
        )

    def _build_system_blocks(
        self,
        rag_context: str = "",
        *,
        skills_prompt: str | None = None,
        recipes_prompt: str = "",
        recipes_before_skills: bool = False,
    ) -> list[SystemBlock]:
        """페르소나·스킬·RAG·ReAct 지시문을 세그먼트(SystemBlock)로 반환한다.

        캐시 경계:
          - 1차: 페르소나 끝 (AGENT.md + USER.md + MEMORY.md)
          - 2차: 스킬 목록 끝
        ReAct 지시문과 RAG 블록은 캐시 마커 뒤에 둔다 (RAG 는 요청마다 변하므로
        무효화 회피, ReAct 는 작아 별도 마커가 불필요).

        Args:
            rag_context: ``_retrieve_relevant_context()`` 결과. 빈 문자열이면 블록을 생략한다.
        """
        # (text, cache) 쌍을 모은 뒤 마지막 블록을 제외한 모든 블록 끝에 separator 를 부착한다.
        segments: list[tuple[str, bool]] = []
        if self._persona_prompt:
            segments.append((self._persona_prompt, True))
        if self._runtime_paths_prompt:
            segments.append((self._runtime_paths_prompt, True))
        effective_skills_prompt = self._skills_prompt if skills_prompt is None else skills_prompt
        if recipes_before_skills and recipes_prompt:
            segments.append((recipes_prompt, False))
        if effective_skills_prompt:
            # recipe 우선 노출 시 동적 recipe 블록 뒤의 skill 블록은 더 이상
            # 정적 prefix가 아니므로 cache marker를 붙이지 않는다.
            segments.append((effective_skills_prompt, not recipes_before_skills))
        if not recipes_before_skills and recipes_prompt:
            segments.append((recipes_prompt, False))
        if rag_context:
            segments.append((rag_context, False))
        segments.append((_TOOL_USAGE_INSTRUCTION, False))

        blocks: list[SystemBlock] = []
        last = len(segments) - 1
        for idx, (text, cache) in enumerate(segments):
            suffix = self._SYSTEM_BLOCK_SEPARATOR if idx < last else ""
            blocks.append(SystemBlock(text=text + suffix, cache=cache))
        return blocks

    @staticmethod
    def _flatten_system_blocks(blocks: list[SystemBlock]) -> str:
        """``_build_system_blocks`` 결과를 단일 문자열로 합친다.

        각 블록 텍스트가 자체 separator 를 포함하므로 빈 문자열로 합쳐도
        기존 ``_build_system_prompt`` 와 byte-identical 한 결과를 낸다.
        """
        return "".join(b.text for b in blocks)

    def _build_system_prompt(self, rag_context: str = "") -> str:
        """레거시 단일-문자열 system prompt API.

        ``_build_system_blocks`` + ``_flatten_system_blocks`` 를 합친 얇은 래퍼.
        BIZ-252 이전 호출자(tests, docs) 호환용. 신규 호출 경로는
        ``_build_system_blocks`` 를 사용해 prompt caching 경계를 보존해야 한다.
        """
        return self._flatten_system_blocks(self._build_system_blocks(rag_context=rag_context))

    async def _retrieve_relevant_context(
        self,
        user_text: str,
        exclude_contents: set[str] | None = None,
    ) -> str:
        """과거 대화 RAG와 Dreaming 장기기억 회수를 service에 위임한다."""
        return await self._context_retrieval.retrieve(user_text, exclude_contents)

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    def _exposable_skills(self) -> list[SkillDefinition]:
        """LLM에 callable로 노출 가능한 스킬만 추린다.

        BIZ-383: ``realtime-lookup-skill`` 은 오케스트레이터가 LLM 루프 밖에서 직접
        실행해 evidence 만 주입하는 내부 스킬이다. LLM이 이를 일반 ``execute_skill``
        대상으로 다시 호출하면 의도와 다른 raw 호출/중복 실행이 생기므로, 프롬프트
        목록과 asset 선택 후보에서 제외한다. 내부 실행은 ``_skills_by_name`` 을 쓰는
        ``_resolve_skill_name`` 으로 그대로 가능하다.
        """
        return [s for s in self._skills if s.name != _REALTIME_LOOKUP_SKILL_NAME]

    def _resolve_skill_name(self, name: str) -> SkillDefinition | None:
        """LLM이 반환한 스킬 이름을 등록된 스킬과 fuzzy-match한다."""
        if name in self._skills_by_name:
            return self._skills_by_name[name]

        lower = name.lower()
        for key, skill in self._skills_by_name.items():
            if key.lower() == lower:
                return skill

        normalized = lower.replace(" ", "-")
        for key, skill in self._skills_by_name.items():
            if key.lower() == normalized:
                return skill

        for key, skill in self._skills_by_name.items():
            if lower.replace("-", "").replace(" ", "") in key.lower().replace("-", ""):
                return skill

        return None

    def _resolve_command_timeout(self, command: str) -> int:
        """명령 timeout 결정을 command_dispatch 에 위임한다."""
        return command_dispatch.resolve_command_timeout(self, command)

    @staticmethod
    def _call_invokes_agent_browser(tool_call: ToolCall) -> bool:
        """agent-browser 호출 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.call_invokes_agent_browser(tool_call)

    @staticmethod
    def _is_agent_browser_command(command: str) -> bool:
        """agent-browser 명령 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.is_agent_browser_command(command)

    @staticmethod
    def _is_composite_agent_browser_chain(command: str) -> bool:
        """agent-browser composite chain 판별을 command_dispatch 에 위임한다."""
        return command_dispatch.is_composite_agent_browser_chain(command)

    @staticmethod
    def _agent_browser_npx_fallback_command(
        command: str, stderr: str,
    ) -> str | None:
        """agent-browser npx fallback 결정을 command_dispatch 에 위임한다."""
        return command_dispatch.agent_browser_npx_fallback_command(command, stderr)

    async def _execute_command(self, skill_name: str, command: str) -> str:
        """셸 명령 실행을 command_dispatch 에 위임한다."""
        return await command_dispatch.execute_command(self, skill_name, command)

    async def _execute_skill(
        self, skill_name: str, args_str: str
    ) -> str | None:
        """등록 스킬 실행을 skill_dispatch 에 위임한다."""
        return await skill_dispatch.execute_registered_skill(self, skill_name, args_str)

    # ------------------------------------------------------------------
    # Skill formatting
    # ------------------------------------------------------------------

    def _format_recipes_for_prompt(self, recipes: list[RecipeDefinition]) -> str:
        """시스템 프롬프트용 레시피 후보 목록을 생성한다.

        selector가 recipe를 고른 경우 main LLM이 `/recipe-name` 명령 경로와 구분해
        레시피 존재 여부만 참고할 수 있도록 읽기 전용 컨텍스트로 노출한다.
        """
        if not recipes:
            return ""
        lines = [*load_system_prompt("recipe_listing").prompt.splitlines(), ""]
        for recipe in recipes:
            desc = recipe.description or recipe.instructions[:160]
            lines.append(f"- **{recipe.name}**: {desc}")
            if recipe.parameters:
                params = ", ".join(param.name for param in recipe.parameters)
                lines.append(f"  Parameters: {params}")
        return "\n".join(lines)

    def _format_skills_for_prompt(self, skills: list[SkillDefinition]) -> str:
        """시스템 프롬프트용 스킬 개요 생성을 skill_dispatch 에 위임한다."""
        return skill_dispatch.format_skills_for_prompt(skills)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_skill_command(self, command: str) -> str:
        """스킬 명령 정규화를 skill_dispatch 에 위임한다."""
        return skill_dispatch.normalize_skill_command(self, command)

    @staticmethod
    def _find_venv_python(script_path: Path) -> Path | None:
        """스크립트 인근 venv python 탐색을 skill_dispatch 에 위임한다."""
        return skill_dispatch.find_venv_python(script_path)

    def _load_skills_config(self) -> dict:
        """config.yaml에서 skills 섹션을 로드한다."""
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}

