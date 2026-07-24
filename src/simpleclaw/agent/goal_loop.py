"""Foreground `/goal` loop with structured judge decisions.

MVP scope:
- `/goal <objective>` runs synchronously inside the current message handler.
- No `/subgoal` mutation of an active run.
- No execution-time `/goal cancel` because Telegram currently awaits foreground handlers.
- No durable resume after process restart.
- Each round uses the existing ToolLoopRunner path; judge only decides whether to continue.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from simpleclaw.agent.progress import (
    ProgressCallback,
    ProgressEvent,
    emit_progress_event,
)
from simpleclaw.agent.tool_loop import ToolLoopResult
from simpleclaw.llm.models import LLMRequest

GoalJudgeStatus = Literal["done", "continue", "blocked"]
GoalConfidence = Literal["low", "medium", "high"]
RunRound = Callable[..., Awaitable[ToolLoopResult]]
JudgeSend = Callable[[LLMRequest], Awaitable[object]]


@dataclass(frozen=True)
class GoalLoopConfig:
    """Runtime budgets for a foreground goal loop."""

    max_rounds: int = 3
    judge_max_tokens: int = 768
    max_answer_chars_for_judge: int = 6000


@dataclass(frozen=True)
class GoalJudgeDecision:
    """Structured judge decision after a goal round."""

    status: GoalJudgeStatus
    reason: str
    missing_criteria: list[str] = field(default_factory=list)
    next_instruction: str = ""
    confidence: GoalConfidence = "low"


@dataclass(frozen=True)
class GoalRound:
    """One execute-and-judge round."""

    index: int
    prompt: str
    answer: str
    trace_count: int
    tool_iterations: int
    judge: GoalJudgeDecision


@dataclass(frozen=True)
class GoalRunResult:
    """Final result returned to the orchestrator."""

    objective: str
    status: GoalJudgeStatus
    final_text: str
    rounds: list[GoalRound]


def _strip_json_fence(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def parse_judge_decision(text: str) -> GoalJudgeDecision:
    """Parse judge JSON safely; malformed output blocks instead of marking done."""

    raw = _strip_json_fence(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return GoalJudgeDecision(
            status="blocked",
            reason=f"Judge response parse failed: {exc}",
            confidence="low",
        )

    if not isinstance(data, dict):
        return GoalJudgeDecision(
            status="blocked",
            reason="Judge response was not a JSON object",
            confidence="low",
        )

    status = str(data.get("status") or "blocked").lower()
    if status not in {"done", "continue", "blocked"}:
        status = "blocked"

    confidence = str(data.get("confidence") or "low").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"

    missing_raw = data.get("missing_criteria", [])
    missing = [str(item) for item in missing_raw] if isinstance(missing_raw, list) else []

    return GoalJudgeDecision(
        status=status,  # type: ignore[arg-type]
        reason=str(data.get("reason") or ""),
        missing_criteria=missing,
        next_instruction=str(data.get("next_instruction") or ""),
        confidence=confidence,  # type: ignore[arg-type]
    )


def build_goal_round_prompt(
    *,
    objective: str,
    round_index: int,
    max_rounds: int,
    previous_rounds: list[GoalRound],
    previous_decision: GoalJudgeDecision | None,
) -> str:
    """Build deterministic prompt for one execution round."""

    history = ""
    if previous_rounds:
        summaries = []
        for goal_round in previous_rounds[-2:]:
            summaries.append(
                f"- Round {goal_round.index}: judge={goal_round.judge.status}, "
                f"reason={goal_round.judge.reason}, "
                f"answer_preview={goal_round.answer[:500]}"
            )
        history = "\n\nPrevious round summary:\n" + "\n".join(summaries)

    continuation = ""
    if previous_decision is not None:
        missing = "\n".join(f"- {item}" for item in previous_decision.missing_criteria)
        continuation = (
            "\n\nJudge says the goal is not complete yet.\n"
            f"Reason: {previous_decision.reason}\n"
            f"Missing criteria:\n{missing or '- (none listed)'}\n"
            "Next instruction: "
            f"{previous_decision.next_instruction or 'Continue toward the goal without repeating completed work.'}"
        )

    return (
        "You are executing an explicit SimpleClaw /goal request.\n"
        f"Round {round_index}/{max_rounds}.\n\n"
        f"목표:\n{objective}\n\n"
        "완료 기준:\n"
        "- 목표에 직접 답한다.\n"
        "- 확인한 근거와 한계를 분리한다.\n"
        "- 필요한 조회를 수행하되 이미 확인한 내용을 불필요하게 반복하지 않는다.\n"
        "- 확실하지 않은 내용은 추측하지 말고 한계로 표시한다.\n"
        f"{history}{continuation}\n\n"
        "이번 round에서 수행할 최선의 다음 행동을 하라."
    )


def _trace_preview(result: ToolLoopResult) -> str:
    lines = []
    for step in result.trace[-8:]:
        lines.append(
            f"- {step.tool_name}: success={step.success}, "
            f"args={step.arguments}, obs={step.observation_preview[:500]}"
        )
    return "\n".join(lines) or "(no tool trace)"


def build_goal_judge_prompt(
    *,
    objective: str,
    round_index: int,
    max_rounds: int,
    answer: str,
    trace_preview: str,
    max_answer_chars: int,
) -> str:
    """Build JSON-only judge prompt."""

    answer_excerpt = answer[:max_answer_chars]
    return (
        "You are the judge for a SimpleClaw /goal loop.\n"
        "Decide whether the original objective has been satisfied by the latest round.\n"
        "Return ONLY a JSON object, no markdown.\n\n"
        f"Objective:\n{objective}\n\n"
        f"Round: {round_index}/{max_rounds}\n\n"
        f"Latest answer:\n{answer_excerpt}\n\n"
        f"Tool trace preview:\n{trace_preview}\n\n"
        "JSON schema:\n"
        '{"status":"done|continue|blocked",'
        '"reason":"short explanation",'
        '"missing_criteria":["..."],'
        '"next_instruction":"specific next instruction if continue",'
        '"confidence":"low|medium|high"}\n\n'
        "Use status=done only if the objective and all obvious completion criteria are satisfied.\n"
        "Use status=continue if another bounded round can likely fix missing criteria.\n"
        "Use status=blocked if the answer cannot be improved without user input, credentials, "
        "destructive actions, or unavailable data."
    )


def format_goal_result(
    objective: str,
    rounds: list[GoalRound],
    final_decision: GoalJudgeDecision,
) -> str:
    """Format the user-visible final goal report."""

    last_answer = rounds[-1].answer if rounds else ""
    status_label = "완료" if final_decision.status == "done" else "중단/부분 완료"
    round_lines = [
        f"- Round {goal_round.index}: judge=`{goal_round.judge.status}`, "
        f"confidence=`{goal_round.judge.confidence}`, "
        f"reason={goal_round.judge.reason or '(없음)'}"
        for goal_round in rounds
    ]
    return (
        f"## /goal 결과 — {status_label}\n"
        f"**목표:** {objective}\n\n"
        f"## 최종 답변\n{last_answer}\n\n"
        f"## Judge 판정\n"
        f"- status: `{final_decision.status}`\n"
        f"- confidence: `{final_decision.confidence}`\n"
        f"- reason: {final_decision.reason or '(없음)'}\n\n"
        "## Round 요약\n"
        + "\n".join(round_lines)
    )


class GoalLoopRunner:
    """Bounded execute/judge loop for explicit `/goal` requests."""

    def __init__(
        self,
        *,
        run_round: RunRound,
        judge_send: JudgeSend,
        config: GoalLoopConfig,
    ) -> None:
        self._run_round = run_round
        self._judge_send = judge_send
        self._config = config

    async def run(
        self,
        objective: str,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> GoalRunResult:
        rounds: list[GoalRound] = []
        previous_decision: GoalJudgeDecision | None = None

        for round_index in range(1, self._config.max_rounds + 1):
            await emit_progress_event(
                on_progress,
                ProgressEvent("goal", f"round-{round_index}", "start", objective),
            )
            prompt = build_goal_round_prompt(
                objective=objective,
                round_index=round_index,
                max_rounds=self._config.max_rounds,
                previous_rounds=rounds,
                previous_decision=previous_decision,
            )
            round_result = await self._run_round(
                prompt,
                on_progress=on_progress,
                on_text_delta=None,
                allow_cron_mutation=False,
            )
            judge_prompt = build_goal_judge_prompt(
                objective=objective,
                round_index=round_index,
                max_rounds=self._config.max_rounds,
                answer=round_result.text,
                trace_preview=_trace_preview(round_result),
                max_answer_chars=self._config.max_answer_chars_for_judge,
            )
            judge_response = await self._judge_send(
                LLMRequest(user_message=judge_prompt, max_tokens=self._config.judge_max_tokens)
            )
            decision = parse_judge_decision(getattr(judge_response, "text", ""))
            goal_round = GoalRound(
                index=round_index,
                prompt=prompt,
                answer=round_result.text,
                trace_count=len(round_result.trace),
                tool_iterations=round_result.iterations,
                judge=decision,
            )
            rounds.append(goal_round)
            await emit_progress_event(
                on_progress,
                ProgressEvent("goal", f"round-{round_index}", "complete", decision.status),
            )

            if decision.status in {"done", "blocked"}:
                return GoalRunResult(
                    objective=objective,
                    status=decision.status,
                    final_text=format_goal_result(objective, rounds, decision),
                    rounds=rounds,
                )
            previous_decision = decision

        exhausted = GoalJudgeDecision(
            status="blocked",
            reason=f"최대 round {self._config.max_rounds}회에 도달했습니다.",
            confidence="medium",
        )
        return GoalRunResult(
            objective=objective,
            status="blocked",
            final_text=format_goal_result(objective, rounds, exhausted),
            rounds=rounds,
        )
