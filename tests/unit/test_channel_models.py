"""채널 모델(channels.models) 단위 테스트.

ChannelError 계층 구조, EventActionType 열거형, AccessAttempt/WebhookEvent
데이터 클래스의 생성·기본값·인스턴스 격리를 검증한다.

주요 테스트 시나리오:
- 에러 클래스 상속 계층이 올바른지 (TelegramError/WebhookError -> ChannelError -> Exception)
- EventActionType 열거형 값과 멤버 수가 기대와 일치하는지
- AccessAttempt의 필수/선택 필드 및 기본값(authorized, timestamp, details)
- WebhookEvent의 필수/선택 필드, 기본값, 그리고 인스턴스 간 payload 격리
"""

from datetime import datetime

import pytest

from simpleclaw.channels.models import (
    AccessAttempt,
    ChannelError,
    EventActionType,
    TelegramError,
    WebhookError,
    WebhookEvent,
)


# --- Error hierarchy ---


class TestErrorHierarchy:
    """채널 에러 클래스의 상속 계층 구조를 검증한다."""

    def test_telegram_error_is_channel_error(self):
        """TelegramError는 ChannelError의 하위 클래스여야 한다."""
        assert issubclass(TelegramError, ChannelError)

    def test_webhook_error_is_channel_error(self):
        """WebhookError는 ChannelError의 하위 클래스여야 한다."""
        assert issubclass(WebhookError, ChannelError)

    def test_channel_error_is_exception(self):
        """ChannelError는 Exception의 하위 클래스여야 한다."""
        assert issubclass(ChannelError, Exception)

    def test_catch_telegram_error_as_channel_error(self):
        """TelegramError를 ChannelError로 catch할 수 있어야 한다 (다형성)."""
        with pytest.raises(ChannelError):
            raise TelegramError("telegram failure")

    def test_catch_webhook_error_as_channel_error(self):
        """WebhookError를 ChannelError로 catch할 수 있어야 한다 (다형성)."""
        with pytest.raises(ChannelError):
            raise WebhookError("webhook failure")

    def test_telegram_error_message(self):
        """에러 메시지가 생성자에 전달한 문자열과 동일해야 한다."""
        err = TelegramError("msg")
        assert str(err) == "msg"


# --- EventActionType ---


class TestEventActionType:
    """EventActionType 열거형의 값과 멤버 수를 검증한다."""

    def test_prompt_value(self):
        """PROMPT 멤버의 값이 문자열 'prompt'여야 한다."""
        assert EventActionType.PROMPT.value == "prompt"

    def test_recipe_value(self):
        """RECIPE 멤버의 값이 문자열 'recipe'여야 한다."""
        assert EventActionType.RECIPE.value == "recipe"

    def test_members_count(self):
        """EventActionType은 정확히 2개의 멤버만 가져야 한다."""
        assert len(EventActionType) == 2


# --- AccessAttempt ---


class TestAccessAttempt:
    """AccessAttempt 데이터 클래스의 생성 및 기본값을 검증한다."""

    def test_creation_with_all_fields(self):
        """모든 필드를 명시적으로 전달하면 해당 값이 그대로 설정되어야 한다."""
        ts = datetime(2026, 1, 1)
        attempt = AccessAttempt(
            source="telegram",
            user_identifier="user123",
            timestamp=ts,
            authorized=True,
            details="ok",
        )
        assert attempt.source == "telegram"
        assert attempt.user_identifier == "user123"
        assert attempt.timestamp == ts
        assert attempt.authorized is True
        assert attempt.details == "ok"

    def test_default_authorized_is_false(self):
        """authorized를 생략하면 기본값 False여야 한다 (보안 기본값: 비인가)."""
        attempt = AccessAttempt(source="webhook", user_identifier="u1")
        assert attempt.authorized is False

    def test_default_timestamp_is_auto_set(self):
        """timestamp를 생략하면 현재 시각이 자동 설정되어야 한다."""
        before = datetime.now()
        attempt = AccessAttempt(source="telegram", user_identifier="u1")
        after = datetime.now()
        # 생성 시점이 before~after 범위 안에 있어야 함
        assert before <= attempt.timestamp <= after

    def test_default_details_is_empty(self):
        """details를 생략하면 빈 문자열이어야 한다."""
        attempt = AccessAttempt(source="telegram", user_identifier="u1")
        assert attempt.details == ""


# --- WebhookEvent ---


class TestWebhookEvent:
    """WebhookEvent 데이터 클래스의 생성, 기본값, 인스턴스 격리를 검증한다."""

    def test_creation_with_all_fields(self):
        """모든 필드를 명시적으로 전달하면 해당 값이 그대로 설정되어야 한다."""
        ts = datetime(2026, 6, 1)
        event = WebhookEvent(
            event_type="push",
            action_type=EventActionType.RECIPE,
            action_reference="recipe-1",
            payload={"key": "val"},
            timestamp=ts,
        )
        assert event.event_type == "push"
        assert event.action_type == EventActionType.RECIPE
        assert event.action_reference == "recipe-1"
        assert event.payload == {"key": "val"}
        assert event.timestamp == ts

    def test_default_payload_is_empty_dict(self):
        """payload를 생략하면 빈 딕셔너리가 기본값이어야 한다."""
        event = WebhookEvent(event_type="ping")
        assert event.payload == {}

    def test_default_action_type_is_none(self):
        """action_type을 생략하면 None이어야 한다 (액션 미지정 상태)."""
        event = WebhookEvent(event_type="ping")
        assert event.action_type is None

    def test_default_action_reference_is_empty(self):
        """action_reference를 생략하면 빈 문자열이어야 한다."""
        event = WebhookEvent(event_type="ping")
        assert event.action_reference == ""

    def test_default_timestamp_is_auto_set(self):
        """timestamp를 생략하면 현재 시각이 자동 설정되어야 한다."""
        before = datetime.now()
        event = WebhookEvent(event_type="ping")
        after = datetime.now()
        assert before <= event.timestamp <= after

    def test_payload_not_shared_between_instances(self):
        """서로 다른 인스턴스의 payload는 독립적이어야 한다 (mutable default 버그 방지)."""
        e1 = WebhookEvent(event_type="a")
        e2 = WebhookEvent(event_type="b")
        # e1의 payload를 변경해도 e2에 영향이 없어야 함
        e1.payload["x"] = 1
        assert "x" not in e2.payload
