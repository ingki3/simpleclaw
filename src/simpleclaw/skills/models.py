"""스킬 시스템의 데이터 모델.

스킬 탐색, 실행, MCP 도구 관리에 사용되는 모든 데이터 클래스와
예외 클래스를 정의한다.

주요 모델:
- SkillDefinition: SKILL.md에서 파싱된 스킬 메타데이터
- SkillResult: 스킬 실행 결과
- ToolDefinition: 스킬/MCP 통합 도구 표현
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum



class SkillScope(Enum):
    """스킬의 로드 출처 (로컬 프로젝트 vs 사용자 전역)."""
    LOCAL = "local"
    GLOBAL = "global"


class ToolSource(Enum):
    """도구의 소스 유형 (스킬 기반 vs MCP 서버 기반)."""
    SKILL = "skill"
    MCP = "mcp"


@dataclass
class RetryPolicy:
    """스킬 실행 실패 시 적용되는 자동 재시도 정책.

    설계 결정:
    - ``idempotent=False``가 기본값. 외부 부수효과(메일 전송, 결제 등)를 가진 스킬을
      자동 재시도해 동일 요청이 중복 실행되는 사고를 막기 위함이다. 사용자가 명시적으로
      "이 스킬은 안전하게 재시도 가능하다"고 선언한 경우에만 자동 재시도가 활성화된다.
    - 백오프는 지수형이며 ``max_backoff_seconds``로 상한을 둔다. 일시적 외부 의존
      장애가 길어질 때 무한 대기 없이 회복 시도/포기 균형을 잡는다.
    - ``retry_on_timeout=False``가 기본값. 타임아웃은 보통 더 깊은 문제(네트워크
      행, 무한 루프 등)를 시사하므로, 타임아웃에서 자동 재시도는 옵트인으로 둔다.
    """

    max_retries: int = 0                  # 최대 재시도 횟수 (0이면 비활성)
    initial_backoff_seconds: float = 1.0  # 첫 재시도 전 대기 시간 (초)
    backoff_factor: float = 2.0           # 지수 증가 계수
    max_backoff_seconds: float = 30.0     # 한 번 대기 시간의 상한
    idempotent: bool = False              # 재시도 안전 여부 (가드)
    retry_on_timeout: bool = False        # 타임아웃 시 재시도 여부

    @property
    def enabled(self) -> bool:
        """이 정책이 자동 재시도를 활성화하는지 판단한다.

        멱등성이 명시되지 않거나 재시도 횟수가 0이면 비활성으로 본다.
        """
        return self.idempotent and self.max_retries > 0

    def compute_backoff(self, attempt: int) -> float:
        """``attempt``번째 재시도 직전에 대기할 초 단위 시간을 계산한다.

        Args:
            attempt: 0부터 시작하는 재시도 인덱스 (0이면 첫 재시도 직전).

        Returns:
            ``initial * factor^attempt``를 ``max_backoff_seconds``로 클램프한 값.
        """
        if attempt < 0:
            attempt = 0
        delay = self.initial_backoff_seconds * (self.backoff_factor ** attempt)
        return min(delay, self.max_backoff_seconds)


@dataclass
class SkillDefinition:
    """SKILL.md에서 파싱된 단일 스킬의 메타데이터."""
    name: str                                        # 스킬 이름
    description: str = ""                            # 스킬 설명
    script_path: str = ""                            # 실행 대상 스크립트 경로
    trigger: str = ""                                # 스킬 발동 조건 (예: 슬래시 커맨드)
    scope: SkillScope = SkillScope.LOCAL             # 로드 출처
    skill_dir: str = ""                              # 스킬 디렉터리 경로
    commands: list[str] = field(default_factory=list) # 코드 블록에서 추출된 명령어
    # 재시도 정책: SKILL.md 프론트매터의 ``retry`` 블록에서 파싱된다. 미지정 시 None
    # — 실행기는 None을 "비활성"으로 간주하므로 기존 스킬은 동작 변경 없이 유지된다.
    retry_policy: RetryPolicy | None = None


@dataclass
class SkillResult:
    """스킬 실행 결과."""
    output: str = ""       # 표준 출력
    exit_code: int = 0     # 프로세스 종료 코드
    error: str = ""        # 표준 에러
    success: bool = True   # 실행 성공 여부
    attempts: int = 1      # 총 실행 시도 횟수 (재시도 포함). 1이면 첫 시도에 성공.


@dataclass
class ToolDefinition:
    """스킬 또는 MCP 도구를 통합 표현하는 모델."""
    name: str                              # 도구 이름
    description: str = ""                  # 도구 설명
    source: ToolSource = ToolSource.SKILL  # 소스 유형
    source_name: str = ""                  # 소스 서버/스킬 이름


class SkillError(Exception):
    """스킬 관련 에러의 기본 클래스."""


class SkillNotFoundError(SkillError):
    """스킬을 찾을 수 없을 때 발생."""


class SkillExecutionError(SkillError):
    """스킬 스크립트 실행이 실패했을 때 발생."""


class SkillTimeoutError(SkillError):
    """스킬 스크립트 실행이 시간 초과했을 때 발생."""


class MCPConnectionError(SkillError):
    """MCP 서버 연결에 실패했을 때 발생."""
