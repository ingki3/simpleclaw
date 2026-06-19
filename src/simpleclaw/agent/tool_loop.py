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
from dataclasses import dataclass, field
from typing import Any

from simpleclaw.agent.clarify import clarify_chat_id_var
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
    "확인은 했지만 답변을 마무리하지 못했습니다. 확인한 결과: {detail}"
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
    return _TOOL_RESULT_EMPTY_FINAL_GENERIC_MESSAGE.format(detail=f"{name}: {detail}")


class ToolLoopRunner:
    """LLM tool-call lifecycle을 반복 실행하는 전용 runner."""

    def __init__(self, orchestrator: Any) -> None:
        """오케스트레이터의 dispatch/router 상태를 재사용하기 위해 참조를 보관한다."""
        self._orchestrator = orchestrator

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
                    "Tool loop [%d] final answer: %d chars",
                    i + 1,
                    len(response.text),
                )
                final_text = (response.text or "").strip()
                if final_text:
                    return ToolLoopResult(final_text, trace=trace, iterations=i + 1)
                if tool_results_for_empty_final:
                    logger.warning(
                        "Tool loop [%d] empty final answer after tool results; "
                        "returning synthesized fallback",
                        i + 1,
                    )
                    return ToolLoopResult(
                        fallback_for_empty_final_after_tools(tool_results_for_empty_final),
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

