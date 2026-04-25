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
class SkillDefinition:
    """SKILL.md에서 파싱된 단일 스킬의 메타데이터."""
    name: str                                        # 스킬 이름
    description: str = ""                            # 스킬 설명
    script_path: str = ""                            # 실행 대상 스크립트 경로
    trigger: str = ""                                # 스킬 발동 조건 (예: 슬래시 커맨드)
    scope: SkillScope = SkillScope.LOCAL             # 로드 출처
    skill_dir: str = ""                              # 스킬 디렉터리 경로
    commands: list[str] = field(default_factory=list) # 코드 블록에서 추출된 명령어


@dataclass
class SkillResult:
    """스킬 실행 결과."""
    output: str = ""       # 표준 출력
    exit_code: int = 0     # 프로세스 종료 코드
    error: str = ""        # 표준 에러
    success: bool = True   # 실행 성공 여부


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
