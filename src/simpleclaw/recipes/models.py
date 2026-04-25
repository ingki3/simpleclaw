"""레시피 시스템의 데이터 모델.

레시피 탐색, 파싱, 실행에 사용되는 모든 데이터 클래스와
예외 클래스를 정의한다.

주요 모델:
- RecipeDefinition: recipe.yaml에서 파싱된 레시피 전체 정의
- RecipeStep: 레시피 내 개별 실행 단계
- RecipeResult / StepResult: 실행 결과
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StepType(Enum):
    """레시피 스텝의 유형."""
    PROMPT = "prompt"    # LLM에 전달할 프롬프트 텍스트
    COMMAND = "command"  # 셸에서 실행할 명령어


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
    step_type: StepType    # 스텝 유형 (PROMPT 또는 COMMAND)
    name: str = ""         # 스텝 이름 (결과 추적용)
    content: str = ""      # 실행 내용 (명령어 또는 프롬프트 텍스트)


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


@dataclass
class StepResult:
    """단일 스텝의 실행 결과."""
    step_name: str         # 실행된 스텝 이름
    success: bool = True   # 성공 여부
    output: str = ""       # 실행 출력
    error: str = ""        # 에러 메시지


@dataclass
class RecipeResult:
    """레시피 전체 실행 결과."""
    recipe_name: str                                               # 레시피 이름
    success: bool = True                                           # 전체 성공 여부
    step_results: list[StepResult] = field(default_factory=list)   # 각 스텝 결과
    failed_step: str = ""                                          # 실패한 스텝 이름
    error: str = ""                                                # 에러 메시지


class RecipeError(Exception):
    """레시피 관련 에러의 기본 클래스."""


class RecipeParseError(RecipeError):
    """레시피 YAML 파싱 또는 유효성 검증 에러."""


class RecipeExecutionError(RecipeError):
    """레시피 스텝 실행 실패 에러."""
