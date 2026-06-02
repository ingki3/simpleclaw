"""BIZ-329 — Telegram tool/recipe progress 이벤트 포맷 검증.

실제 런타임 이벤트를 사용자에게 짧게 보여주되, token/password 같은 값은 절대
노출하지 않아야 한다. 이 테스트는 새 progress 모듈의 public contract 를 고정한다.
"""

from __future__ import annotations

from simpleclaw.agent.progress import ProgressEvent, format_progress_line, redact_secrets


def test_redact_secret_like_key_values() -> None:
    """token/password/api_key 형태의 값은 compact preview 에서 마스킹된다."""
    text = "curl -H 'Authorization: Bearer abc123SECRET' --data password=hunter2 api_key=sk-live"

    redacted = redact_secrets(text)

    assert "abc123SECRET" not in redacted
    assert "hunter2" not in redacted
    assert "sk-live" not in redacted
    assert "[REDACTED]" in redacted


def test_format_tool_progress_line_is_compact_and_redacted() -> None:
    """도구 시작 이벤트는 종류/이름/preview 를 한 줄로 압축한다."""
    event = ProgressEvent(
        kind="tool",
        name="cli",
        status="start",
        detail={"command": "echo token=secret-value"},
    )

    line = format_progress_line(event)

    assert line.startswith("🛠️ cli 시작")
    assert "secret-value" not in line
    assert "[REDACTED]" in line
    assert "\n" not in line


def test_format_recipe_progress_line_mentions_recipe_status() -> None:
    """레시피 이벤트도 도구와 별도 종류로 표시된다."""
    line = format_progress_line(
        ProgressEvent(kind="recipe", name="daily", status="complete", detail="2 steps")
    )

    assert line == "📋 daily 완료 — 2 steps"
