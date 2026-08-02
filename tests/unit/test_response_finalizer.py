from simpleclaw.agent.response_finalizer import ResponseFinalizer
from simpleclaw.agent.result_validator import ValidationDecision


def test_unknown_effect_finalizer_states_no_retry() -> None:
    text = ResponseFinalizer().finalize(
        ValidationDecision(False, (), (), (), "unknown_effect")
    )
    assert "다시 실행하지 않았" in text

