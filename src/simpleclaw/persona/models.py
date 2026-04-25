"""페르소나 파싱 엔진의 데이터 모델.

마크다운 페르소나 파일을 구조화된 객체로 표현하기 위한
Enum과 dataclass 정의를 담고 있다.

주요 모델:
- FileType / SourceScope: 파일 종류·출처 구분 열거형
- Section: 마크다운 헤딩 단위의 섹션
- PersonaFile: 파싱된 하나의 페르소나 파일
- PromptAssembly: 여러 파일을 조합한 최종 시스템 프롬프트
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FileType(Enum):
    """페르소나 파일의 유형.

    AGENT: 에이전트 행동 지시 (최우선)
    USER: 사용자별 설정·선호
    MEMORY: 대화 기억·맥락
    """
    AGENT = "agent"
    USER = "user"
    MEMORY = "memory"


class SourceScope(Enum):
    """페르소나 파일의 로드 출처.

    LOCAL: 프로젝트 로컬 디렉터리 (우선순위 높음)
    GLOBAL: 사용자 전역 디렉터리 (우선순위 낮음)
    """
    LOCAL = "local"
    GLOBAL = "global"


@dataclass(frozen=True)
class Section:
    """마크다운 헤딩 하나와 그 본문 내용.

    Attributes:
        level: 헤딩 수준 (1=h1, 2=h2, ...). 헤딩 없는 프리앰블은 0.
        title: 헤딩 텍스트. 프리앰블이면 빈 문자열.
        content: 헤딩 아래 본문 텍스트.
    """
    level: int
    title: str
    content: str


@dataclass
class PersonaFile:
    """파싱된 하나의 페르소나 마크다운 파일.

    Attributes:
        file_type: 파일 유형 (AGENT / USER / MEMORY).
        source_path: 원본 파일 경로.
        source_scope: 로드 출처 (LOCAL / GLOBAL).
        sections: 파싱된 섹션 목록.
        raw_content: 원본 마크다운 텍스트.
    """
    file_type: FileType
    source_path: str
    source_scope: SourceScope
    sections: list[Section] = field(default_factory=list)
    raw_content: str = ""


@dataclass
class PromptAssembly:
    """여러 페르소나 파일을 조합한 최종 시스템 프롬프트.

    Attributes:
        parts: 조합에 사용된 PersonaFile 목록.
        assembled_text: 조합·절삭된 최종 프롬프트 텍스트.
        token_count: 최종 텍스트의 토큰 수.
        token_budget: 허용된 최대 토큰 예산.
        was_truncated: 토큰 예산 초과로 절삭되었는지 여부.
    """
    parts: list[PersonaFile] = field(default_factory=list)
    assembled_text: str = ""
    token_count: int = 0
    token_budget: int = 0
    was_truncated: bool = False
