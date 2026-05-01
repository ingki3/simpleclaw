"""서브에이전트 응답 프로토콜 검증 테스트.

`validate_response`가 정상/비정상 stdout을 모두 안전하게 분류하고,
`SubAgentResponse`의 분기별 일관성 검증이 의도대로 동작하는지 확인한다.
"""

from __future__ import annotations

import json

import pytest

from simpleclaw.agents.protocol import (
    SubAgentErrorDetail,
    SubAgentResponse,
    ValidationFailure,
    validate_response,
)


class TestSubAgentResponse:
    def test_success_with_data(self):
        resp = SubAgentResponse.model_validate(
            {"status": "success", "data": {"answer": 42}}
        )
        assert resp.status == "success"
        assert resp.data == {"answer": 42}
        assert resp.error is None

    def test_error_with_string_error(self):
        resp = SubAgentResponse.model_validate(
            {"status": "error", "error": "boom"}
        )
        assert resp.status == "error"
        assert resp.error == "boom"
        assert resp.error_text() == "boom"

    def test_error_with_structured_detail(self):
        resp = SubAgentResponse.model_validate(
            {
                "status": "error",
                "error": {
                    "code": "E_TIMEOUT",
                    "message": "took too long",
                    "details": {"elapsed": 30},
                },
            }
        )
        assert isinstance(resp.error, SubAgentErrorDetail)
        assert resp.error.code == "E_TIMEOUT"
        assert resp.error_text() == "[E_TIMEOUT] took too long"

    def test_error_status_requires_error_field(self):
        with pytest.raises(ValueError, match="requires non-empty 'error'"):
            SubAgentResponse.model_validate({"status": "error"})

    def test_success_status_rejects_error_field(self):
        with pytest.raises(ValueError, match="must not include 'error'"):
            SubAgentResponse.model_validate(
                {"status": "success", "error": "oops"}
            )

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            SubAgentResponse.model_validate({"status": "weird"})

    def test_meta_passes_through(self):
        resp = SubAgentResponse.model_validate(
            {
                "status": "success",
                "data": {},
                "meta": {"agent_id": "abc", "version": "1.0"},
            }
        )
        assert resp.meta == {"agent_id": "abc", "version": "1.0"}

    def test_extra_fields_ignored(self):
        # 향후 호환성을 위해 알 수 없는 필드는 무시한다.
        resp = SubAgentResponse.model_validate(
            {"status": "success", "data": {}, "future_field": "ignored"}
        )
        assert resp.status == "success"


class TestValidateResponse:
    def test_valid_success(self):
        result = validate_response(
            json.dumps({"status": "success", "data": {"x": 1}})
        )
        assert isinstance(result, SubAgentResponse)
        assert result.status == "success"
        assert result.data == {"x": 1}

    def test_valid_error(self):
        result = validate_response(
            json.dumps({"status": "error", "error": "bad"})
        )
        assert isinstance(result, SubAgentResponse)
        assert result.status == "error"
        assert result.error_text() == "bad"

    def test_strips_surrounding_whitespace(self):
        result = validate_response(
            "\n  " + json.dumps({"status": "success"}) + "  \n"
        )
        assert isinstance(result, SubAgentResponse)

    def test_empty_output(self):
        result = validate_response("")
        assert isinstance(result, ValidationFailure)
        assert result.reason == "empty_output"

    def test_whitespace_only_treated_as_empty(self):
        result = validate_response("   \n\t  ")
        assert isinstance(result, ValidationFailure)
        assert result.reason == "empty_output"

    def test_invalid_json(self):
        result = validate_response("this is not json")
        assert isinstance(result, ValidationFailure)
        assert result.reason == "invalid_json"
        assert "this is not json" in result.raw

    def test_partial_json(self):
        result = validate_response('{"status": "success", "data":')
        assert isinstance(result, ValidationFailure)
        assert result.reason == "invalid_json"

    def test_top_level_array_rejected(self):
        result = validate_response('[{"status": "success"}]')
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"
        assert "object" in result.message

    def test_top_level_string_rejected(self):
        result = validate_response('"hello"')
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"

    def test_missing_status(self):
        result = validate_response(json.dumps({"data": {}}))
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"

    def test_invalid_status_value(self):
        result = validate_response(
            json.dumps({"status": "weird", "data": {}})
        )
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"

    def test_error_status_without_error_field(self):
        result = validate_response(json.dumps({"status": "error"}))
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"

    def test_raw_excerpt_truncated(self):
        big = "x" * 2000
        # 잘못된 JSON으로 처리되되 raw는 raw_limit 이하로 잘려야 한다.
        result = validate_response(big, raw_limit=100)
        assert isinstance(result, ValidationFailure)
        assert result.reason == "invalid_json"
        assert len(result.raw) == 100

    def test_data_must_be_object(self):
        result = validate_response(
            json.dumps({"status": "success", "data": [1, 2, 3]})
        )
        assert isinstance(result, ValidationFailure)
        assert result.reason == "schema_violation"

    def test_meta_preserved_through_validation(self):
        result = validate_response(
            json.dumps(
                {
                    "status": "success",
                    "data": {},
                    "meta": {"trace_id": "xyz"},
                }
            )
        )
        assert isinstance(result, SubAgentResponse)
        assert result.meta == {"trace_id": "xyz"}

    def test_structured_error_detail_preserved(self):
        result = validate_response(
            json.dumps(
                {
                    "status": "error",
                    "error": {"code": "E1", "message": "bad", "details": {"k": "v"}},
                }
            )
        )
        assert isinstance(result, SubAgentResponse)
        assert isinstance(result.error, SubAgentErrorDetail)
        assert result.error.details == {"k": "v"}

    def test_failure_includes_raw_for_debugging(self):
        result = validate_response('{"status": "weird"}')
        assert isinstance(result, ValidationFailure)
        assert '"weird"' in result.raw
