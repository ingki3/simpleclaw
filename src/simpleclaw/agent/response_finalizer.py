"""Validated final, claim-limited response, clarify를 일관되게 생성한다."""

from __future__ import annotations

from collections.abc import Callable

from simpleclaw.agent.result_validator import ValidationDecision


class ResponseFinalizer:
    """Common validator 결정 밖의 claim을 composer에 전달하지 않는다."""

    def finalize(
        self,
        decision: ValidationDecision,
        *,
        draft: str = "",
        clarify_question: str = "",
        compose: Callable[[tuple[str, ...]], str] | None = None,
    ) -> str:
        if clarify_question:
            return clarify_question.strip()
        if decision.allow_final:
            if compose is not None:
                composed = compose(decision.supported_claims).strip()
                if composed:
                    return composed
            if draft.strip():
                return draft.strip()
            return "확인된 결과를 바탕으로 요청을 처리했습니다."
        if decision.action_state == "unknown_effect":
            return (
                "외부 작업의 적용 여부를 확인할 수 없어 자동으로 다시 실행하지 "
                "않았습니다. 현재 상태를 확인한 뒤 재시도해 주세요."
            )
        if decision.action_state == "partial_success":
            return (
                "외부 작업이 일부만 완료되어 중복 실행을 피하기 위해 자동 재시도하지 "
                "않았습니다. 완료된 항목을 먼저 확인해 주세요."
            )
        blocked = ", ".join(decision.blocked_claims)
        if blocked:
            return f"근거가 충분하지 않아 확정 답변을 제한합니다. 추가 확인 필요: {blocked}"
        return "현재 확인된 근거만으로는 요청을 확정적으로 완료할 수 없습니다."
