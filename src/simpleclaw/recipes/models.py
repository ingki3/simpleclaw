"""레시피 시스템의 데이터 모델.

레시피 탐색, 파싱, 실행에 사용되는 모든 데이터 클래스와
예외 클래스를 정의한다.

주요 모델:
- RecipeDefinition: recipe.yaml에서 파싱된 레시피 전체 정의
- RecipeStep: 레시피 내 개별 실행 단계
- RecipeResult / StepResult: 실행 결과
- StepStatus: 단계별 부분 실패 처리를 위한 상태(success/skipped/failed)
- OnErrorPolicy: 실패 시 정책(abort/continue/rollback)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepType(Enum):
    """레시피 스텝의 유형."""
    PROMPT = "prompt"    # LLM에 전달할 프롬프트 텍스트
    COMMAND = "command"  # 셸에서 실행할 명령어


class StepStatus(Enum):
    """단계 실행 상태.

    - SUCCESS: 정상 완료
    - SKIPPED: 실행되지 않음 (resume 이전 / abort 후 / 정책에 의한 건너뜀)
    - FAILED: 실행 중 실패
    """
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class OnErrorPolicy(Enum):
    """스텝 실패 시 처리 정책.

    - ABORT: (기본) 즉시 중단하고 이후 스텝은 SKIPPED 처리
    - CONTINUE: 실패를 기록하고 다음 스텝을 계속 실행
    - ROLLBACK: 즉시 중단하고 이미 성공한 스텝의 ``rollback`` 명령을 역순 실행
    """
    ABORT = "abort"
    CONTINUE = "continue"
    ROLLBACK = "rollback"


@dataclass
class RecipeParameter:
    """레시피에 정의된 파라미터."""
    name: str              # 파라미터 이름 (${name}으로 참조)
    description: str = ""  # 파라미터 설명
    required: bool = True  # 필수 여부
    default: str = ""      # 기본값 (required=False일 때 사용)


@dataclass
class RecipeStep:
    """레시피 내 단일 실행 단계."""
    step_type: StepType                       # 스텝 유형 (PROMPT 또는 COMMAND)
    name: str = ""                            # 스텝 이름 (결과 추적용)
    content: str = ""                         # 실행 내용 (명령어 또는 프롬프트 텍스트)
    on_error: OnErrorPolicy | None = None     # 스텝 단위 실패 정책 (None이면 레시피 기본값)
    rollback: str = ""                        # 롤백 시 실행할 셸 명령 (선택)


@dataclass
class RecipeDefinition:
    """recipe.yaml에서 파싱된 레시피 전체 정의.

    두 가지 포맷을 지원한다:
    - 스텝 기반: ``steps`` 필드 사용 (레거시)
    - 지시문 기반: ``instructions`` 필드 사용 (v2)
    """
    name: str                                                       # 레시피 이름
    description: str = ""                                           # 레시피 설명
    parameters: list[RecipeParameter] = field(default_factory=list) # 입력 파라미터 목록
    steps: list[RecipeStep] = field(default_factory=list)           # 실행 스텝 목록
    instructions: str = ""                                          # v2 지시문 텍스트
    recipe_dir: str = ""                                            # 레시피 디렉터리 경로
    on_error: OnErrorPolicy = OnErrorPolicy.ABORT                   # 기본 실패 정책


@dataclass
class StepResult:
    """단일 스텝의 실행 결과.

    ``status`` 가 단계별 상태의 단일 출처(single source of truth)이며,
    ``success`` 는 호환성을 위한 파생 프로퍼티다.

    에러는 두 채널로 분리한다:
    - ``error``     : 사용자에게 노출 가능한 한 줄 요약(짧고 안전)
    - ``debug_log`` : 디버깅용 상세 로그(stdout/stderr 전체, 트레이스백 등)
    """
    step_name: str                                  # 실행된 스텝 이름
    status: StepStatus = StepStatus.SUCCESS         # 실행 상태
    output: str = ""                                # 실행 출력 (성공 시)
    error: str = ""                                 # 사용자 노출용 에러 요약
    debug_log: str = ""                             # 디버그용 상세 로그

    @property
    def success(self) -> bool:
        """SUCCESS 상태 여부 (기존 ``success`` 필드와의 호환을 위한 프로퍼티)."""
        return self.status == StepStatus.SUCCESS

    @property
    def skipped(self) -> bool:
        """SKIPPED 상태 여부."""
        return self.status == StepStatus.SKIPPED

    @property
    def failed(self) -> bool:
        """FAILED 상태 여부."""
        return self.status == StepStatus.FAILED


@dataclass
class RecipeResult:
    """레시피 전체 실행 결과.

    ``error_summary`` 는 사용자에게 보일 수 있는 짧은 요약(여러 줄 가능),
    ``debug_log`` 는 운영자가 볼 상세 로그를 제공한다.

    ``resumable_from`` 은 실패 시 ``execute_recipe(..., resume_from=...)``
    로 재시작 가능한 첫 실패 스텝의 이름을 가리킨다.
    """
    recipe_name: str                                                # 레시피 이름
    success: bool = True                                            # 전체 성공 여부
    step_results: list[StepResult] = field(default_factory=list)    # 각 스텝 결과
    failed_step: str = ""                                           # 첫 번째 실패 스텝 이름
    failed_steps: list[str] = field(default_factory=list)           # 실패한 모든 스텝 이름
    error: str = ""                                                 # (호환) 첫 실패 에러 요약
    error_summary: str = ""                                         # 사용자 노출용 종합 에러 요약
    debug_log: str = ""                                             # 디버그용 상세 로그(스텝별 누적)
    resumable_from: str = ""                                        # 재실행 시작 지점(스텝 이름)
    rollback_results: list[StepResult] = field(default_factory=list)  # 롤백 단계 실행 결과


class RecipeError(Exception):
    """레시피 관련 에러의 기본 클래스."""


class RecipeParseError(RecipeError):
    """레시피 YAML 파싱 또는 유효성 검증 에러."""


class RecipeExecutionError(RecipeError):
    """레시피 스텝 실행 실패 에러."""
