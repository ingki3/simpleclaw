import pytest

from simpleclaw.agent.goal_loop import (
    GoalJudgeDecision,
    GoalLoopConfig,
    GoalLoopRunner,
    build_goal_judge_prompt,
    build_goal_round_prompt,
    parse_judge_decision,
)
from simpleclaw.agent.tool_loop import ToolLoopResult, ToolTraceStep
from simpleclaw.llm.models import LLMResponse


def test_parse_judge_decision_plain_json():
    decision = parse_judge_decision(
        '{"status":"continue","reason":"missing data",'
        '"missing_criteria":["sources"],'
        '"next_instruction":"check sources","confidence":"medium"}'
    )

    assert decision.status == "continue"
    assert decision.reason == "missing data"
    assert decision.missing_criteria == ["sources"]
    assert decision.next_instruction == "check sources"
    assert decision.confidence == "medium"


def test_parse_judge_decision_strips_code_fence():
    decision = parse_judge_decision(
        '```json\n{"status":"done","reason":"ok"}\n```'
    )

    assert decision.status == "done"
    assert decision.reason == "ok"


def test_parse_judge_decision_invalid_json_blocks_safely():
    decision = parse_judge_decision("not json")

    assert decision.status == "blocked"
    assert "parse" in decision.reason.lower()


def test_parse_judge_decision_unknown_status_blocks():
    decision = parse_judge_decision('{"status":"maybe","reason":"?"}')

    assert decision.status == "blocked"


def test_build_first_goal_round_prompt_contains_objective_and_budget():
    prompt = build_goal_round_prompt(
        objective="로그 원인 찾기",
        round_index=1,
        max_rounds=3,
        previous_rounds=[],
        previous_decision=None,
    )

    assert "로그 원인 찾기" in prompt
    assert "Round 1/3" in prompt
    assert "완료 기준" in prompt


def test_build_continuation_prompt_uses_judge_next_instruction():
    decision = GoalJudgeDecision(
        status="continue",
        reason="근거 부족",
        missing_criteria=["최근 로그"],
        next_instruction="최근 bot.log를 확인하라",
        confidence="medium",
    )

    prompt = build_goal_round_prompt(
        objective="원인 찾기",
        round_index=2,
        max_rounds=3,
        previous_rounds=[],
        previous_decision=decision,
    )

    assert "최근 bot.log를 확인하라" in prompt
    assert "최근 로그" in prompt


def test_build_judge_prompt_requires_json_schema():
    prompt = build_goal_judge_prompt(
        objective="원인 찾기",
        round_index=1,
        max_rounds=3,
        answer="답변",
        trace_preview="web_search: ok",
        max_answer_chars=6000,
    )

    assert '"status"' in prompt
    assert "done" in prompt
    assert "continue" in prompt
    assert "blocked" in prompt
    assert "원인 찾기" in prompt


@pytest.mark.asyncio
async def test_goal_loop_stops_when_judge_done():
    round_prompts = []

    async def run_round(prompt, **kwargs):
        round_prompts.append(prompt)
        return ToolLoopResult(
            text="최종 원인을 찾았습니다.",
            trace=[ToolTraceStep("log_debug", {}, "ok")],
            iterations=1,
        )

    async def judge(request):
        return LLMResponse(text='{"status":"done","reason":"충족","confidence":"high"}')

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=3),
    )
    result = await runner.run("로그 원인 찾아줘")

    assert result.status == "done"
    assert len(result.rounds) == 1
    assert "최종 원인" in result.final_text
    assert len(round_prompts) == 1


@pytest.mark.asyncio
async def test_goal_loop_continues_until_done():
    calls = {"rounds": 0, "judges": 0}

    async def run_round(prompt, **kwargs):
        calls["rounds"] += 1
        return ToolLoopResult(text=f"answer {calls['rounds']}", iterations=1)

    async def judge(request):
        calls["judges"] += 1
        if calls["judges"] == 1:
            return LLMResponse(
                text=(
                    '{"status":"continue","reason":"부족",'
                    '"missing_criteria":["추가 확인"],'
                    '"next_instruction":"추가 확인을 하라",'
                    '"confidence":"medium"}'
                )
            )
        return LLMResponse(text='{"status":"done","reason":"충족","confidence":"high"}')

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=3),
    )
    result = await runner.run("목표")

    assert result.status == "done"
    assert len(result.rounds) == 2


@pytest.mark.asyncio
async def test_goal_loop_returns_partial_when_max_rounds_exhausted():
    async def run_round(prompt, **kwargs):
        return ToolLoopResult(text="partial", iterations=1)

    async def judge(request):
        return LLMResponse(
            text=(
                '{"status":"continue","reason":"still missing",'
                '"next_instruction":"continue","confidence":"low"}'
            )
        )

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=2),
    )
    result = await runner.run("목표")

    assert result.status == "blocked"
    assert len(result.rounds) == 2
    assert "최대 round" in result.final_text


@pytest.mark.asyncio
async def test_goal_loop_blocks_on_invalid_judge_json():
    async def run_round(prompt, **kwargs):
        return ToolLoopResult(text="answer", iterations=1)

    async def judge(request):
        return LLMResponse(text="I think it is done")

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=3),
    )
    result = await runner.run("목표")

    assert result.status == "blocked"
    assert len(result.rounds) == 1
    assert "parse" in result.final_text.lower()


@pytest.mark.asyncio
async def test_goal_loop_emits_progress_events():
    events = []

    async def on_progress(event):
        events.append((event.kind, event.name, event.status))

    async def run_round(prompt, **kwargs):
        return ToolLoopResult(text="answer", iterations=1)

    async def judge(request):
        return LLMResponse(text='{"status":"done","reason":"ok","confidence":"high"}')

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=1),
    )
    await runner.run("목표", on_progress=on_progress)

    assert ("goal", "round-1", "start") in events
    assert ("goal", "round-1", "complete") in events


@pytest.mark.asyncio
async def test_second_round_prompt_includes_previous_judge_instruction():
    prompts = []

    async def run_round(prompt, **kwargs):
        prompts.append(prompt)
        return ToolLoopResult(text=f"answer {len(prompts)}", iterations=1)

    judge_calls = {"n": 0}

    async def judge(request):
        judge_calls["n"] += 1
        if judge_calls["n"] == 1:
            return LLMResponse(
                text=(
                    '{"status":"continue","reason":"need source",'
                    '"missing_criteria":["공식 출처"],'
                    '"next_instruction":"공식 출처를 확인하고 표로 정리",'
                    '"confidence":"medium"}'
                )
            )
        return LLMResponse(text='{"status":"done","reason":"ok","confidence":"high"}')

    runner = GoalLoopRunner(
        run_round=run_round,
        judge_send=judge,
        config=GoalLoopConfig(max_rounds=3),
    )
    await runner.run("목표")

    assert len(prompts) == 2
    assert "공식 출처를 확인하고 표로 정리" in prompts[1]
    assert "공식 출처" in prompts[1]


def test_goal_channel_is_auto_trigger_channel():
    from simpleclaw.memory.models import CHANNEL_GOAL_PREFIX, is_auto_trigger_channel

    assert CHANNEL_GOAL_PREFIX == "goal:"
    assert is_auto_trigger_channel("goal:manual") is True
