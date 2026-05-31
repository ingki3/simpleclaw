"""BIZ-316 AGENT.md dreaming update 필터 테스트."""

from __future__ import annotations

from simpleclaw.memory.agent_update_filter import filter_agent_updates


def test_filter_agent_updates_drops_cron_recipe_event_logs() -> None:
    """레시피/크론 완료 기록은 AGENT.md가 아니라 MEMORY.md 책임이다."""
    raw = "\n".join(
        [
            "- `usstock-night` 크론 작업의 주기를 23:30으로 변경함",
            "- link-to-wiki 레시피를 생성함",
        ]
    )

    assert filter_agent_updates(raw) == ""


def test_filter_agent_updates_keeps_durable_behavior_policy() -> None:
    """앞으로 적용할 지속 행동 규칙은 AGENT.md 갱신 대상으로 유지한다."""
    raw = "- 앞으로 주식 레시피를 만들 때는 시장 상태, API 값, 뉴스 원문을 교차검증한다."

    assert filter_agent_updates(raw) == raw


def test_filter_agent_updates_removes_memory_duplicates() -> None:
    """MEMORY.md에 이미 들어갈 사건 기록과 의미가 겹치면 AGENT.md에서 제거한다."""
    raw = "\n".join(
        [
            "- 앞으로 주식 레시피를 만들 때는 시장 상태, API 값, 뉴스 원문을 교차검증한다.",
            "- usstock-night 크론 작업의 주기를 23:30으로 변경함",
        ]
    )
    memory = "- usstock-night 크론 작업의 주기를 22:30에서 23:30으로 변경함"

    assert filter_agent_updates(raw, memory_text=memory) == (
        "- 앞으로 주식 레시피를 만들 때는 시장 상태, API 값, 뉴스 원문을 교차검증한다."
    )


def test_filter_agent_updates_keeps_only_policy_bullets_from_mixed_input() -> None:
    """혼합 입력에서는 정책 bullet만 남긴다."""
    raw = "\n".join(
        [
            "- 2026-05-20에 ai-report 레시피에 4단계 검증 프로세스를 반영함",
            "- 앞으로 ai-report는 최신 모델명 같은 민감한 정보는 반드시 웹 검색으로 검증한다.",
            "- 사용자의 요청에 따라 check_new_emails 크론 작업을 매 정시로 변경함",
        ]
    )

    assert filter_agent_updates(raw) == (
        "- 앞으로 ai-report는 최신 모델명 같은 민감한 정보는 반드시 웹 검색으로 검증한다."
    )
