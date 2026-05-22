"""Unit tests for the clarify tool module (BIZ-260).

Coverage:
- Option normalization (str / dict / mixed, validation errors, label cap).
- Callback data encoding / decoding round trip + 64-byte boundary guard.
- ``ClarifyRequest.format_user_visible`` keeps options enumerated for the next
  LLM turn (DoD: backward compat with text "1"/"2" replies).
- ``handle_clarify`` builtin handler: chat_id gating, validation surface,
  registry side effects.
"""

from __future__ import annotations

import pytest

from simpleclaw.agent.builtin_tools import handle_clarify
from simpleclaw.agent.clarify import (
    MAX_BUTTON_LABEL_CHARS,
    MAX_CALLBACK_DATA_BYTES,
    MAX_CLARIFY_OPTIONS,
    ClarifyOption,
    ClarifyRequest,
    decode_callback_data,
    encode_callback_data,
    normalize_options,
)


class TestNormalizeOptions:
    def test_string_list(self):
        opts = normalize_options(["Foo", "Bar", "Baz"])
        assert len(opts) == 3
        assert opts[0] == ClarifyOption(index=0, label="Foo", body="Foo")
        assert opts[2].index == 2
        assert opts[2].body == "Baz"

    def test_dict_list_with_label_and_body(self):
        opts = normalize_options(
            [
                {"label": "📧 Foo", "body": "Foo Bar Baz the long body"},
                {"label": "Bar", "body": "Bar"},
            ]
        )
        assert opts[0].label == "📧 Foo"
        assert opts[0].body == "Foo Bar Baz the long body"
        assert opts[1].label == "Bar"

    def test_dict_label_only_uses_label_as_body(self):
        opts = normalize_options([{"label": "Only label"}])
        assert opts[0].body == "Only label"
        assert opts[0].label == "Only label"

    def test_empty_options_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            normalize_options([])

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            normalize_options("not a list")  # type: ignore[arg-type]

    def test_too_many_options_raises(self):
        too_many = [f"opt {i}" for i in range(MAX_CLARIFY_OPTIONS + 1)]
        with pytest.raises(ValueError, match="at most"):
            normalize_options(too_many)

    def test_label_longer_than_cap_truncated_with_ellipsis(self):
        long_body = "x" * (MAX_BUTTON_LABEL_CHARS + 20)
        opts = normalize_options([long_body])
        assert len(opts[0].label) == MAX_BUTTON_LABEL_CHARS
        assert opts[0].label.endswith("…")
        # 본문은 그대로 보존 — 라벨만 자른다.
        assert opts[0].body == long_body

    def test_empty_body_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_options(["  "])

    def test_unsupported_item_type_raises(self):
        with pytest.raises(ValueError, match="string or"):
            normalize_options([123])  # type: ignore[list-item]


class TestCallbackData:
    def test_encode_decode_roundtrip(self):
        for idx in range(MAX_CLARIFY_OPTIONS):
            payload = encode_callback_data(idx)
            assert decode_callback_data(payload) == idx

    def test_encode_format_is_prefix_index(self):
        assert encode_callback_data(0) == "c:0"
        assert encode_callback_data(7) == "c:7"

    def test_payload_within_64_bytes(self):
        # 가장 긴 케이스(7) 도 64 byte 이하여야 한다 — DoD §"길이 64 byte boundary".
        payload = encode_callback_data(MAX_CLARIFY_OPTIONS - 1)
        assert len(payload.encode("utf-8")) <= MAX_CALLBACK_DATA_BYTES

    def test_decode_rejects_unknown_prefix(self):
        assert decode_callback_data("x:0") is None
        assert decode_callback_data("c0") is None  # missing colon
        assert decode_callback_data("") is None

    def test_decode_rejects_negative_or_garbage_suffix(self):
        assert decode_callback_data("c:-1") is None
        assert decode_callback_data("c:abc") is None
        assert decode_callback_data("c:") is None

    def test_decode_rejects_non_string(self):
        assert decode_callback_data(None) is None  # type: ignore[arg-type]
        assert decode_callback_data(123) is None  # type: ignore[arg-type]


class TestClarifyRequestFormatting:
    def test_format_user_visible_keeps_question_and_numbered_options(self):
        request = ClarifyRequest(
            question="어느 메일에 답장?",
            options=normalize_options(["받은편지함의 A", "받은편지함의 B"]),
        )
        text = request.format_user_visible()
        assert text.startswith("어느 메일에 답장?")
        assert "1. 받은편지함의 A" in text
        assert "2. 받은편지함의 B" in text


class TestHandleClarify:
    def test_records_pending_request_for_known_chat(self):
        registry: dict = {}
        result = handle_clarify(
            {"question": "어느 거?", "options": ["A", "B"]},
            registry,
            chat_id=42,
        )
        assert 42 in registry
        assert registry[42].question == "어느 거?"
        assert len(registry[42].options) == 2
        # tool result 는 LLM 에게 "끝" 임을 명확히 알리는 문구여야 한다.
        assert "Clarification posted" in result

    def test_rejects_when_chat_id_missing(self):
        registry: dict = {}
        result = handle_clarify(
            {"question": "?", "options": ["A"]},
            registry,
            chat_id=None,
        )
        assert result.startswith("Error:")
        assert registry == {}

    def test_validates_empty_question(self):
        registry: dict = {}
        result = handle_clarify(
            {"question": "  ", "options": ["A"]},
            registry,
            chat_id=1,
        )
        assert result.startswith("Error:")
        assert "question" in result.lower()
        assert registry == {}

    def test_validates_empty_options(self):
        registry: dict = {}
        result = handle_clarify(
            {"question": "?", "options": []},
            registry,
            chat_id=1,
        )
        assert result.startswith("Error:")
        assert registry == {}

    def test_validates_too_many_options(self):
        registry: dict = {}
        too_many = [f"opt {i}" for i in range(MAX_CLARIFY_OPTIONS + 1)]
        result = handle_clarify(
            {"question": "?", "options": too_many},
            registry,
            chat_id=1,
        )
        assert result.startswith("Error:")
        assert registry == {}

    def test_overwrites_prior_request_for_same_chat(self):
        """동일 chat 에서 새 clarify 호출은 이전 요청을 덮어쓴다.

        한 turn 안에 LLM 이 clarify 를 두 번 부를 일은 정상 아니지만, 그렇게 됐
        을 때 마지막 호출만 사용자에게 도달하도록 일관 동작을 보장한다.
        """
        registry: dict = {}
        handle_clarify(
            {"question": "first", "options": ["A"]},
            registry, chat_id=1,
        )
        handle_clarify(
            {"question": "second", "options": ["B", "C"]},
            registry, chat_id=1,
        )
        assert registry[1].question == "second"
        assert [o.body for o in registry[1].options] == ["B", "C"]
