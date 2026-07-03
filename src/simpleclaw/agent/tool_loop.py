"""Native Function Calling tool loop runner.

이 모듈은 AgentOrchestrator에서 tool-call lifecycle만 분리한다. 오케스트레이터는
프롬프트/컨텍스트/자산 선택을 준비하고, runner는 LLM 호출 → tool 실행 → observation
추가 → forced-final fallback까지의 반복 제어를 전담한다.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from simpleclaw.agent.clarify import (
    ClarifyRequest,
    clarify_chat_id_var,
    normalize_options,
)
from simpleclaw.agent.file_mutation_tracker import format_footer
from simpleclaw.agent.progress import ProgressCallback, ProgressEvent, emit_progress_event
from simpleclaw.llm.models import LLMRequest, SystemBlock, ToolCall
from simpleclaw.llm.providers.base import TextDeltaCallback
from simpleclaw.security.sanitize import sanitize_tool_output

logger = logging.getLogger("simpleclaw.agent.orchestrator")

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
_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE = "확인해 봤지만 관련 기록을 찾지 못했습니다."
_TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE = (
    "확인 중 오류가 발생해 답변을 마무리하지 못했습니다: {detail}"
)
_TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE = (
    "확인한 근거는 있지만 답을 확정하는 데 필요한 설명을 마무리하지 못했습니다.\n"
    "우선 확인된 단서는 다음과 같습니다: {detail}\n"
    "확정 답변에 필요한 조건이 남아 있다면 "
    "키워드, 출처, 시간·장소, 확인할 URL 같은 단서를 더 알려주세요."
)
_EMPTY_FINAL_RECOVERY_QUESTION = (
    "확인한 범위만으로는 답을 확정하기 어렵습니다. "
    "추가로 어떤 방향으로 확인할까요?"
)
_EMPTY_FINAL_RECOVERY_OPTIONS = [
    {"label": "다른 키워드", "body": "다른 키워드로 다시 확인해줘"},
    {"label": "다른 출처", "body": "다른 출처에서 다시 확인해줘"},
    {"label": "조건 추가", "body": "시간·장소·대상 조건을 추가해서 확인해줘"},
    {"label": "URL로 확인", "body": "내가 주는 URL 기준으로 확인해줘"},
]
_TOOL_RESULT_EMPTY_FINAL_NEEDS_CLARIFICATION_MESSAGE = (
    _EMPTY_FINAL_RECOVERY_QUESTION
    + "\n\n1. 다른 키워드로 다시 확인해줘"
    + "\n2. 다른 출처에서 다시 확인해줘"
    + "\n3. 시간·장소·대상 조건을 추가해서 확인해줘"
    + "\n4. 내가 주는 URL 기준으로 확인해줘"
)

_TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS = (
    "0 chars",
    "0 rows",
    "0 row",
    "no rows",
    "not found",
    "no results",
    "검색 결과가 없습니다",
    "찾을 수 없습니다",
    "없습니다",
    "[]",
    "{}",
)
_TOOL_RESULT_EMPTY_FINAL_ERROR_PREFIXES = (
    "error",
    "failed",
    "exception",
    "traceback",
    "command failed",
    "skill failed",
    "도구 실행 실패",
)
_TOOL_RESULT_EMPTY_FINAL_NO_EVIDENCE_MARKERS = (
    "[command completed with no output]",
    "command completed with no output",
    "[no output]",
    "no output from command",
)
_TOOL_RESULT_EMPTY_FINAL_META_RESULT_MARKERS = (
    "[skill documentation for",
    "skill documentation for",
    "browser automation for interactive website tasks",
    "use this skill when",
)
_TOOL_RESULT_EMPTY_FINAL_META_TOOL_NAMES = frozenset({"skill_docs"})
# web_search observation 은 ``WEB_SEARCH_RESULTS:`` 헤더로 시작하고,
# 각 결과가 ``N. Title`` + ``URL: ...`` 줄로 렌더된다(builtin_tools._format_web_search_results).
# 빈 final fallback 이 이 근거를 240자 truncation 으로 뭉개지 않도록 별도 파서를 둔다.
_WEB_SEARCH_RESULTS_MARKER = "WEB_SEARCH_RESULTS:"
_WEB_SEARCH_ENTRY_TITLE_RE = re.compile(r"^\d+\.\s+(.*)$")
_WEB_SEARCH_EVIDENCE_MAX_ENTRIES = 5
_TOOL_RESULT_EMPTY_FINAL_WEB_EVIDENCE_MESSAGE = (
    "검색은 마쳤지만 답변을 매끄럽게 정리하지 못했습니다.\n"
    "확인한 검색 결과는 다음과 같으니 참고해 주세요:\n{evidence}\n"
    "원하시면 특정 결과를 더 자세히 확인하거나, 조건을 좁혀 다시 찾아드리겠습니다."
)
_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS = 30.0
_FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE = (
    "응답이 지연되어 처리를 종료했습니다. 죄송하지만 한 번 더 말씀해 주세요. "
    "(debug: final-answer LLM 호출이 {timeout:.0f}초 안에 응답하지 않음)"
)
_AGENT_BROWSER_PER_TURN_CALL_CAP = 2
_AGENT_BROWSER_CAP_EXCEEDED_MESSAGE = (
    "Error: `agent-browser` has already been attempted {count} times in this "
    "turn and is being rate-limited to avoid exhausting the tool loop. If the "
    "page text could not be retrieved by `web_fetch` (which already includes a "
    "headless fallback), the site is blocking automated fetching. Reply to the "
    "user that the page cannot be retrieved rather than retrying with "
    "agent-browser, cli, or another skill."
)
_FILE_MUTATING_TOOLS = frozenset({"file_write", "file_manage", "execute_skill", "cli"})


def _compat_constant(name: str, default: Any) -> Any:
    """기존 테스트/운영 monkeypatch가 orchestrator module constant를 바꾸면 반영한다."""
    try:
        from simpleclaw.agent import orchestrator as orch_mod

        return getattr(orch_mod, name, default)
    except Exception:  # noqa: BLE001
        return default


@dataclass
class ToolLoopState:
    """한 turn의 tool loop 실행에 필요한 불변/가변 상태 묶음."""

    user_content: str
    messages: list[dict[str, Any]]
    system_prompt: str
    tools: list[dict[str, Any]]
    system_blocks: list[SystemBlock]
    live_evidence_seen: bool = False
    live_fact_requires_evidence: bool = False
    previous_mutation_snapshot: dict[str, Any] | None = None
    on_text_delta: TextDeltaCallback | None = None
    on_progress: ProgressCallback | None = None
    operator_tools: bool = False
    allow_cron_mutation: bool = True


@dataclass
class ToolTraceStep:
    """학습/진단용 tool 실행 snapshot.

    사용자 응답에는 영향을 주지 않으며, arguments와 observation은 이미 redaction 및
    길이 제한된 preview만 담아 후속 skill-learning hook이 원본 민감값을 저장하지
    않도록 한다.
    """

    tool_name: str
    arguments: dict[str, Any]
    observation_preview: str
    success: bool = True


@dataclass
class ToolLoopResult:
    """ToolLoopRunner가 오케스트레이터 wrapper에 돌려주는 최종 결과."""

    text: str
    trace: list[ToolTraceStep] = field(default_factory=list)
    iterations: int = 0
    success: bool = True




def _legacy_react_action_to_tool_call(text: str) -> ToolCall | None:
    """구 ReAct ``Action: {...}`` 응답을 execute_skill ToolCall로 변환한다.

    현재 런타임은 Native Function Calling이 정식 경로지만, 오래된 시나리오
    fixture와 일부 CLI provider는 텍스트 Action JSON을 반환할 수 있다. 명시적 JSON
    객체에 ``skill_name``과 ``command``가 모두 있을 때만 호환 변환한다.
    """
    marker = "Action:"
    if marker not in text:
        return None
    payload = text.split(marker, 1)[1].strip().splitlines()[0].strip()
    try:
        args = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None
    if not args.get("skill_name") or not args.get("command"):
        return None
    return ToolCall(id="legacy_react_action", name="execute_skill", arguments=args)


def _legacy_observation_text(tool_call: ToolCall, sanitized_result: str) -> str:
    """ReAct 호환 fixture가 기대하는 user_message Observation 문자열을 만든다."""
    skill_name = str(tool_call.arguments.get("skill_name") or "unknown-skill")
    return f"Observation: execute_skill({skill_name}) result:\n{sanitized_result[:3000]}"


def _tool_result_looks_like_explicit_error(content: str) -> bool:
    """도구 결과가 명시적 오류 envelope/header 로 시작하는지 판정한다."""
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


def _tool_result_looks_like_not_found(content: str) -> bool:
    """도구 결과가 명시적인 empty/not-found 결과인지 판정한다."""
    stripped = content.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return any(
        marker in lowered or marker in stripped
        for marker in _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MARKERS
    )


def _tool_result_looks_like_no_evidence(content: str) -> bool:
    """성공처럼 끝났지만 답변 근거가 전혀 없는 observation인지 판정한다."""
    stripped = content.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    return any(
        marker in lowered for marker in _TOOL_RESULT_EMPTY_FINAL_NO_EVIDENCE_MARKERS
    )


def _tool_result_looks_like_meta_result(name: str, content: str) -> bool:
    """도구 사용법/문서처럼 사용자 질문의 답 근거가 아닌 observation인지 판정한다."""
    tool_name = (name or "").strip().lower()
    if tool_name in _TOOL_RESULT_EMPTY_FINAL_META_TOOL_NAMES:
        return True
    lowered = content.strip().lower()
    return any(
        marker in lowered for marker in _TOOL_RESULT_EMPTY_FINAL_META_RESULT_MARKERS
    )


def _tool_result_is_useful_for_empty_final(name: str, content: str) -> bool:
    """빈 final fallback에서 사용자에게 보존할 만한 observation인지 판정한다."""
    return not (
        _tool_result_looks_like_meta_result(name, content)
        or _tool_result_looks_like_explicit_error(content)
        or _tool_result_looks_like_no_evidence(content)
        or _tool_result_looks_like_not_found(content)
    )


def _is_web_search_results_payload(content: str) -> bool:
    """observation 이 web_search 결과 렌더링(WEB_SEARCH_RESULTS)인지 판정한다."""
    return content.strip().startswith(_WEB_SEARCH_RESULTS_MARKER)


def _extract_web_search_entries(content: str) -> list[tuple[str, str]]:
    """WEB_SEARCH_RESULTS 페이로드에서 (title, url) 쌍을 등장 순서대로 뽑는다.

    ``N. Title`` 줄로 제목을 잡고 바로 이어지는 ``URL:`` 줄에서 링크를 회수한다.
    title 이 비어 있으면 URL 자체를 라벨로 쓰도록 caller 가 처리한다.
    """
    entries: list[tuple[str, str]] = []
    pending_title: str | None = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        title_match = _WEB_SEARCH_ENTRY_TITLE_RE.match(line)
        if title_match is not None:
            pending_title = title_match.group(1).strip()
            continue
        if pending_title is not None and line.lower().startswith("url:"):
            url = line[len("url:"):].strip()
            if url:
                entries.append((pending_title, url))
            pending_title = None
    return entries


def _collect_web_search_evidence(
    tool_results: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """유용한 web_search observation 전체에서 title/url 근거를 dedup 하여 모은다.

    마지막 한 건이 아니라 이번 turn 에서 확인된 모든 검색 결과를 보존하려는 목적이다.
    오류/무결과/메타 문서 성격의 web_search observation 은 사용자 근거에서 제외한다.
    """
    evidence: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for name, content in tool_results:
        if not _is_web_search_results_payload(content):
            continue
        if not _tool_result_is_useful_for_empty_final(name, content):
            continue
        for title, url in _extract_web_search_entries(content):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            evidence.append((title or url, url))
    return evidence


def _format_web_search_evidence(evidence: list[tuple[str, str]]) -> str:
    """확인된 title/url 근거를 사용자에게 바로 쓸 수 있는 목록 메시지로 만든다."""
    lines = [
        f"- {title}\n  {url}"
        for title, url in evidence[:_WEB_SEARCH_EVIDENCE_MAX_ENTRIES]
    ]
    return _TOOL_RESULT_EMPTY_FINAL_WEB_EVIDENCE_MESSAGE.format(
        evidence="\n".join(lines),
    )


def build_empty_final_recovery_clarify_request() -> ClarifyRequest:
    """근거 부족 fallback에서 사용자에게 다음 탐색 방향을 묻는 요청을 만든다."""
    return ClarifyRequest(
        question=_EMPTY_FINAL_RECOVERY_QUESTION,
        options=normalize_options(_EMPTY_FINAL_RECOVERY_OPTIONS),
    )


def _redacted_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """trace snapshot에 저장할 tool arguments에서 secret-like 값을 마스킹한다."""
    redacted: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if any(marker in str(key).lower() for marker in ("token", "secret", "password", "api_key", "apikey")):
            redacted[str(key)] = "[REDACTED_SECRET]"
        elif isinstance(value, dict):
            redacted[str(key)] = _redacted_arguments(value)
        elif isinstance(value, str):
            redacted[str(key)] = value[:500]
        else:
            redacted[str(key)] = value
    return redacted


def fallback_for_empty_final_after_tools(tool_results: list[tuple[str, str]]) -> str:
    """도구 실행 후 LLM final 텍스트가 비었을 때 사용자 가시 fallback을 만든다."""
    if not tool_results:
        return _EMPTY_DIRECT_RESPONSE_MESSAGE

    # web_search 근거가 있으면 마지막 observation 하나를 240자로 자르는 generic 경로 대신,
    # 이번 turn 에서 확인된 title/URL 목록을 그대로 보존해 사용자에게 확인 근거를 넘긴다.
    web_evidence = _collect_web_search_evidence(tool_results)
    if web_evidence:
        return _format_web_search_evidence(web_evidence)

    # 한 turn 안에서 모델이 유효한 검색/조회 결과를 받은 뒤 추가 재검색을 하다가
    # transient 오류나 no-output 명령으로 끝나는 경우가 있다. 빈 final fallback이
    # 무조건 마지막 tool result만 보면 이미 확인한 사실을 버리고 오류/무근거만 노출한다.
    # 그래서 뒤에서부터 보되, 명시적 오류·빈 결과·무근거가 아닌 가장 최근 observation을
    # 우선 사용한다. 모든 observation이 유용하지 않을 때만 오류/못 찾음/추가질문으로 분기한다.
    useful = next(
        (
            (candidate_name, candidate_content)
            for candidate_name, candidate_content in reversed(tool_results)
            if _tool_result_is_useful_for_empty_final(
                candidate_name,
                candidate_content,
            )
        ),
        None,
    )

    if useful is None:
        error_result = next(
            (
                candidate_content
                for _, candidate_content in reversed(tool_results)
                if _tool_result_looks_like_explicit_error(candidate_content)
            ),
            None,
        )
        if error_result is not None:
            detail = error_result.strip().splitlines()[0][:240]
            return _TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE.format(detail=detail)
        if any(_tool_result_looks_like_no_evidence(content) for _, content in tool_results):
            return _TOOL_RESULT_EMPTY_FINAL_NEEDS_CLARIFICATION_MESSAGE
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    name, content = useful
    stripped = content.strip()
    if not stripped:
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    if _tool_result_looks_like_explicit_error(stripped):
        detail = stripped.splitlines()[0][:240]
        return _TOOL_RESULT_EMPTY_FINAL_ERROR_MESSAGE.format(detail=detail)

    if _tool_result_looks_like_not_found(stripped):
        return _TOOL_RESULT_EMPTY_FINAL_NOT_FOUND_MESSAGE

    detail = stripped.replace("\n", " ")[:240]
    return _TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE.format(detail=f"{name}: {detail}")


class ToolLoopRunner:
    """LLM tool-call lifecycle을 반복 실행하는 전용 runner."""

    def __init__(self, orchestrator: Any) -> None:
        """오케스트레이터의 dispatch/router 상태를 재사용하기 위해 참조를 보관한다."""
        self._orchestrator = orchestrator

    def _maybe_set_empty_final_recovery_clarify(self, fallback_text: str) -> str:
        """대화형 채널이면 근거 부족 fallback을 pending clarify로 전환한다."""
        if fallback_text != _TOOL_RESULT_EMPTY_FINAL_NEEDS_CLARIFICATION_MESSAGE:
            return fallback_text
        chat_id = clarify_chat_id_var.get()
        if chat_id is None:
            return fallback_text
        self._orchestrator._pending_clarify[chat_id] = (
            build_empty_final_recovery_clarify_request()
        )
        return ""

    async def run(self, state: ToolLoopState) -> ToolLoopResult:
        """LLM 호출과 tool observation 누적을 반복하고 최종 텍스트를 반환한다."""
        invoked_tool_sequence: list[str] = []
        tool_results_for_empty_final: list[tuple[str, str]] = []
        trace: list[ToolTraceStep] = []
        agent_browser_call_count = 0
        prev_snapshot = state.previous_mutation_snapshot

        for i in range(self._orchestrator._max_tool_iterations):
            try:
                request = LLMRequest(
                    system_prompt=state.system_prompt,
                    user_message=state.user_content,
                    messages=state.messages,
                    tools=state.tools,
                    system_blocks=state.system_blocks,
                )
                text_delta_callback = state.on_text_delta
                if text_delta_callback is not None:
                    response = await self._orchestrator._router.send(
                        request, on_text_delta=text_delta_callback,
                    )
                else:
                    response = await self._orchestrator._router.send(request)
            except Exception as exc:  # noqa: BLE001
                logger.error("Tool loop LLM error: %s", exc)
                return ToolLoopResult(
                    f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}",
                    trace=trace,
                    iterations=i + 1,
                    success=False,
                )

            legacy_action = None
            if not response.tool_calls:
                legacy_action = _legacy_react_action_to_tool_call(response.text or "")
                if legacy_action is not None:
                    response.tool_calls = [legacy_action]

            if not response.tool_calls:
                logger.info(
                    "Tool loop [%d] final answer: %d chars finish_reason=%s usage=%s",
                    i + 1,
                    len(response.text),
                    response.finish_reason,
                    response.usage,
                )
                final_text = (response.text or "").strip()
                if final_text:
                    return ToolLoopResult(final_text, trace=trace, iterations=i + 1)
                if tool_results_for_empty_final:
                    logger.warning(
                        "Tool loop [%d] empty final answer after tool results; "
                        "returning synthesized fallback finish_reason=%s diagnostics=%s",
                        i + 1,
                        response.finish_reason,
                        response.diagnostics,
                    )
                    fallback_text = fallback_for_empty_final_after_tools(
                        tool_results_for_empty_final,
                    )
                    return ToolLoopResult(
                        self._maybe_set_empty_final_recovery_clarify(fallback_text),
                        trace=trace,
                        iterations=i + 1,
                    )
                return ToolLoopResult(_EMPTY_DIRECT_RESPONSE_MESSAGE, trace=trace, iterations=i + 1)

            logger.info("Tool loop [%d] %d tool call(s)", i + 1, len(response.tool_calls))
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
            if response.raw_assistant_message is not None:
                assistant_msg["_raw_content"] = response.raw_assistant_message
            state.messages.append(assistant_msg)

            for tc in response.tool_calls:
                invoked_tool_sequence.append(tc.name)
                logger.info(
                    "Tool call: %s(%s)",
                    tc.name,
                    json.dumps(tc.arguments, ensure_ascii=False)[:200],
                )

                if self._orchestrator._call_invokes_agent_browser(tc):
                    agent_browser_call_count += 1
                    if agent_browser_call_count > _compat_constant("_AGENT_BROWSER_PER_TURN_CALL_CAP", _AGENT_BROWSER_PER_TURN_CALL_CAP):
                        result = _AGENT_BROWSER_CAP_EXCEEDED_MESSAGE.format(
                            count=agent_browser_call_count - 1,
                        )
                        logger.warning(
                            "BIZ-190: agent-browser per-turn cap exceeded "
                            "(%d > %d); synthesizing blocked response",
                            agent_browser_call_count - 1,
                            _compat_constant("_AGENT_BROWSER_PER_TURN_CALL_CAP", _AGENT_BROWSER_PER_TURN_CALL_CAP),
                        )
                        state.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": result[:3000],
                        })
                        trace.append(
                            ToolTraceStep(
                                tool_name=tc.name,
                                arguments=_redacted_arguments(tc.arguments),
                                observation_preview=sanitize_tool_output(result)[:1200],
                                success=False,
                            )
                        )
                        continue

                progress_kind, progress_name = self._orchestrator._progress_identity_for_tool_call(tc)
                await emit_progress_event(
                    state.on_progress,
                    ProgressEvent(progress_kind, progress_name, "start", tc.arguments),
                )
                try:
                    dispatch = self._orchestrator._dispatch_tool_call
                    dispatch_params = inspect.signature(dispatch).parameters
                    dispatch_kwargs: dict[str, Any] = {}
                    if "operator_tools" in dispatch_params:
                        dispatch_kwargs["operator_tools"] = state.operator_tools
                    if "allow_cron_mutation" in dispatch_params:
                        dispatch_kwargs["allow_cron_mutation"] = state.allow_cron_mutation
                    if dispatch_kwargs:
                        result = await dispatch(tc, **dispatch_kwargs)
                    else:
                        # 기존 테스트/플러그인이 _dispatch_tool_call(tc) 형태로
                        # monkeypatch한 경우를 보존한다.
                        result = await dispatch(tc)
                except Exception as exc:  # noqa: BLE001
                    await emit_progress_event(
                        state.on_progress,
                        ProgressEvent(progress_kind, progress_name, "fail", str(exc)),
                    )
                    trace.append(
                        ToolTraceStep(
                            tool_name=tc.name,
                            arguments=_redacted_arguments(tc.arguments),
                            observation_preview=f"Error: {str(exc)[:1200]}",
                            success=False,
                        )
                    )
                    raise
                await emit_progress_event(
                    state.on_progress,
                    ProgressEvent(progress_kind, progress_name, "complete", result),
                )
                sanitized = sanitize_tool_output(result)
                trace.append(
                    ToolTraceStep(
                        tool_name=tc.name,
                        arguments=_redacted_arguments(tc.arguments),
                        observation_preview=sanitized[:1200],
                        success=not _tool_result_looks_like_explicit_error(sanitized),
                    )
                )
                tool_results_for_empty_final.append((tc.name, sanitized))
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": sanitized[:3000],
                })
                if legacy_action is not None and tc.id == legacy_action.id:
                    state.user_content = (
                        f"{state.user_content}\n"
                        f"{_legacy_observation_text(tc, sanitized)}"
                    )
                logger.info("Tool result: %s → %d chars", tc.name, len(sanitized))

            chat_id_for_clarify = clarify_chat_id_var.get()
            if (
                chat_id_for_clarify is not None
                and chat_id_for_clarify in self._orchestrator._pending_clarify
            ):
                logger.info(
                    "Tool loop [%d] terminated by clarify call (chat=%d)",
                    i + 1,
                    chat_id_for_clarify,
                )
                return ToolLoopResult("", trace=trace, iterations=i + 1)

            prev_snapshot = self._append_mutation_footer(
                state=state,
                response_tool_calls=response.tool_calls,
                prev_snapshot=prev_snapshot,
            )

        forced_text = await self._force_final_answer(state, invoked_tool_sequence)
        return ToolLoopResult(forced_text, trace=trace, iterations=self._orchestrator._max_tool_iterations)

    def _append_mutation_footer(
        self,
        *,
        state: ToolLoopState,
        response_tool_calls: list[ToolCall],
        prev_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """tool observation 마지막 항목에 파일 변경 footer를 best-effort로 부착한다."""
        if prev_snapshot is None:
            return None
        try:
            new_snapshot = self._orchestrator._mutation_tracker.snapshot(
                previous=prev_snapshot,
            )
            file_diff = self._orchestrator._mutation_tracker.diff(prev_snapshot, new_snapshot)
            footer = format_footer(file_diff)
            iteration_had_mutating_call = any(
                tc.name in _FILE_MUTATING_TOOLS for tc in response_tool_calls
            )
            if not footer and iteration_had_mutating_call:
                footer = "[file changes this turn: none]"
            if footer and state.messages and state.messages[-1].get("role") == "tool":
                state.messages[-1]["content"] = state.messages[-1]["content"] + "\n\n" + footer
            return new_snapshot
        except Exception as exc:  # noqa: BLE001
            logger.warning("FileMutationTracker footer 부착 실패: %s", exc)
            return prev_snapshot

    async def _force_final_answer(
        self,
        state: ToolLoopState,
        invoked_tool_sequence: list[str],
    ) -> str:
        """tool iteration 예산 소진 시 tools 없는 final LLM 호출을 수행한다."""
        logger.warning(
            "Tool loop max iterations (%d) reached, forcing final answer; tool_sequence=%s",
            self._orchestrator._max_tool_iterations,
            invoked_tool_sequence,
        )
        try:
            final_request = LLMRequest(
                system_prompt=state.system_prompt,
                user_message=state.user_content,
                messages=state.messages,
                system_blocks=state.system_blocks,
            )
            if state.on_text_delta is not None:
                final_send = self._orchestrator._router.send(
                    final_request,
                    on_text_delta=state.on_text_delta,
                )
            else:
                final_send = self._orchestrator._router.send(final_request)
            final_response = await asyncio.wait_for(
                final_send,
                timeout=_compat_constant("_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS", _FORCED_FINAL_ANSWER_TIMEOUT_SECONDS),
            )
        except asyncio.TimeoutError:
            logger.error(
                "Tool loop final generation timeout after %ss",
                _FORCED_FINAL_ANSWER_TIMEOUT_SECONDS,
            )
            return _FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE.format(
                timeout=_compat_constant("_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS", _FORCED_FINAL_ANSWER_TIMEOUT_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Tool loop final generation error: %s", exc)
            return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

        final_text = (final_response.text or "").strip()
        if not final_text:
            return _BUDGET_EXHAUSTED_EMPTY_MESSAGE.format(
                iterations=self._orchestrator._max_tool_iterations,
            )
        return (
            f"{final_text}\n\n"
            + _BUDGET_EXHAUSTED_HINT_SUFFIX.format(
                iterations=self._orchestrator._max_tool_iterations,
            )
        )

