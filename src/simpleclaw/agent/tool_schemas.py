"""내장 도구 및 외부 스킬의 Function Calling 스키마 레지스트리.

Native Function Calling에 사용할 ToolDefinition 목록을 조립한다.
내장 도구 7종의 고정 스키마와, 외부 스킬을 위한 execute_skill 함수 1종을 정의한다.

설계 결정:
  - 외부 스킬은 개별 함수로 등록하지 않고, 단일 execute_skill 함수에
    skill_name 인자로 디스패치 (Hermes Agent 방식). 스킬 추가/삭제 시
    스키마 재생성이 불필요하여 hot-reload와 호환됨.
  - 도구 이름은 API 호환성을 위해 언더스코어 사용 (web_fetch 등).
    orchestrator에서 핸들러 매핑 시 이 이름을 그대로 사용.
"""

from __future__ import annotations

from simpleclaw.llm.models import ToolDefinition
from simpleclaw.skills.models import SkillDefinition


# ---------------------------------------------------------------------------
# 내장 도구 스키마 정의 (7종)
# ---------------------------------------------------------------------------

_CLI_TOOL = ToolDefinition(
    name="cli",
    description="터미널 명령어를 실행한다. 파일 검색, 시스템 정보 확인, 스크립트 실행 등에 활용. "
                "위험한 명령어(rm -rf, git force push 등)는 보안 가드에 의해 차단된다.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "실행할 셸 명령어",
            },
        },
        "required": ["command"],
    },
)

_WEB_FETCH_TOOL = ToolDefinition(
    name="web_fetch",
    description="URL에서 웹 페이지 내용을 가져온다. 뉴스 기사, API 응답, 문서 등을 읽을 수 있다. "
                "HTTP GET 요청만 지원하며, 내부 네트워크(localhost, 10.x 등)는 차단된다. "
                "정적 HTML 본문이 짧으면 자동으로 헤드리스 브라우저(agent-browser)로 폴백한다. "
                "JS 렌더링이 필요한 SPA/동적 페이지임을 미리 알면 force_headless=True 로 즉시 헤드리스 경로 사용.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "가져올 URL",
            },
            "force_headless": {
                "type": "boolean",
                "description": "True 이면 정적 fetch 를 skip 하고 곧바로 헤드리스 브라우저로 렌더링 (기본값: false).",
            },
        },
        "required": ["url"],
    },
)

_FILE_READ_TOOL = ToolDefinition(
    name="file_read",
    description="파일 내용을 읽는다. 텍스트 파일, 설정 파일, 로그 등을 확인할 수 있다. "
                "프로젝트 디렉토리 내의 파일만 읽을 수 있다.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "읽을 파일 경로 (프로젝트 루트 기준)",
            },
            "offset": {
                "type": "integer",
                "description": "시작 줄 번호 (0부터, 음수면 파일 끝에서부터)",
            },
            "limit": {
                "type": "integer",
                "description": "읽을 줄 수 (기본값: 200)",
            },
        },
        "required": ["path"],
    },
)

_FILE_WRITE_TOOL = ToolDefinition(
    name="file_write",
    description="파일에 내용을 쓴다. workspace 디렉토리(.agent/workspace/) 내에서만 쓰기 가능. "
                "새 파일 생성 또는 기존 파일 덮어쓰기/추가(append)를 지원한다.",
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "쓸 파일 경로 (workspace 내)",
            },
            "content": {
                "type": "string",
                "description": "쓸 내용",
            },
            "append": {
                "type": "boolean",
                "description": "True이면 파일 끝에 추가, False(기본)이면 덮어쓰기",
            },
        },
        "required": ["path", "content"],
    },
)

_FILE_MANAGE_TOOL = ToolDefinition(
    name="file_manage",
    description="파일 시스템 관리 작업: 디렉토리 목록 조회, 생성, 삭제, 파일 정보 확인.",
    parameters={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["list", "mkdir", "delete", "info"],
                "description": "수행할 작업 종류",
            },
            "path": {
                "type": "string",
                "description": "대상 경로",
            },
        },
        "required": ["operation", "path"],
    },
)

_SKILL_DOCS_TOOL = ToolDefinition(
    name="skill_docs",
    description="사용자가 설치한 스킬의 사용법 문서(SKILL.md)를 조회한다. "
                "스킬을 처음 사용하기 전에 반드시 이 도구로 문서를 확인해야 한다.",
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "조회할 스킬 이름",
            },
        },
        "required": ["name"],
    },
)

# BIZ-325 — Active Memory 온디맨드 검색 도구. 자동 RAG 주입만으로 부족할 때
# LLM이 별도 질의어로 장기기억/과거 대화를 명시 회상하도록 노출한다.
_SEARCH_MEMORY_TOOL = ToolDefinition(
    name="search_memory",
    description=(
        "과거 대화와 DB-backed 장기기억을 의미 기반으로 검색한다. 최근 대화나 "
        "현재 시스템 프롬프트에 없는 사용자 선호, 프로젝트 맥락, 과거 결정이 "
        "필요할 때 사용한다. 실시간 사실, 파일 내용, 시스템 상태 확인에는 사용하지 "
        "말고 해당 전용 도구를 사용한다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "찾고 싶은 기억/과거 대화의 자연어 질의",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "반환할 최대 항목 수 (1~10, 기본값은 메모리 설정 사용)",
            },
        },
        "required": ["query"],
    },
)

# BIZ-260 — clarify 다지선다 도구. 텔레그램 등 인라인 키보드를 지원하는 채널은
# ``options`` 를 버튼으로 렌더한다. 호출 즉시 ReAct 루프가 종결되어 LLM 의 다음
# 텍스트 응답을 기다리지 않는다 — clarify 의 본 의도가 "사용자에게 되묻기" 이므로
# 후속 도구 호출 / 텍스트 응답은 의미가 없다.
_CLARIFY_TOOL = ToolDefinition(
    name="clarify",
    description=(
        "사용자에게 다지선다 질문을 던진다. 답변 후보가 명확히 짧고 셀 수 있을 때 "
        "(예: '어느 메일/캘린더 이벤트/파일을 선택?') 사용. 채널이 지원하면 인라인 "
        "키보드 버튼으로 렌더되고, 사용자가 버튼 탭 또는 텍스트('1'/'2'/본문) 로 "
        "답하면 다음 메시지로 도착한다. 호출 즉시 이 turn 은 종료되며 LLM 의 "
        "후속 응답 / 도구 호출은 발생하지 않는다 — clarify 호출이 곧 사용자에게 "
        "되묻는 행위 자체이다. 자유형 질문(이름·주제 등) 에는 사용하지 말 것."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "사용자에게 보여줄 짧은 질문 문구.",
            },
            "options": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "string",
                    "description": "옵션 본문 (버튼 라벨 + 사용자 응답에 사용).",
                },
                "description": (
                    "1~8개의 다지선다 옵션. 각 항목은 짧은 문자열이며 모바일 버튼"
                    "에 들어가도록 가능하면 30자 이내로."
                ),
            },
        },
        "required": ["question", "options"],
    },
)


_CRON_TOOL = ToolDefinition(
    name="cron",
    description="크론 스케줄 관리. 반복/예약 작업을 등록, 조회, 삭제, 활성화/비활성화한다. "
                "사용자가 '~시에 ~해줘', '매일 ~시에 알려줘' 같은 요청을 하면 이 도구를 사용한다.",
    parameters={
        "type": "object",
        "properties": {
            "cron_action": {
                "type": "string",
                "enum": ["add", "list", "remove", "enable", "disable"],
                "description": "수행할 크론 작업 종류",
            },
            "name": {
                "type": "string",
                "description": "크론 작업 이름 (add/remove/enable/disable 시 필수)",
            },
            "cron_expression": {
                "type": "string",
                "description": "5-field cron 표현식 (분 시 일 월 요일). 예: '15 20 * * *' = 매일 오후 8:15",
            },
            "action_type": {
                "type": "string",
                "enum": ["recipe", "prompt"],
                "description": "recipe(레시피 실행) 또는 prompt(프롬프트를 LLM에 전달)",
            },
            "action_reference": {
                "type": "string",
                "description": "recipe일 때는 레시피 파일 경로, prompt일 때는 실행할 프롬프트 텍스트",
            },
        },
        "required": ["cron_action"],
    },
)


# ---------------------------------------------------------------------------
# 빌더 함수
# ---------------------------------------------------------------------------

def build_tool_definitions(
    skills: list[SkillDefinition],
    cron_available: bool = False,
) -> list[ToolDefinition]:
    """현재 상태에 맞는 ToolDefinition 목록을 조립한다.

    Args:
        skills: 등록된 외부 스킬 목록.
        cron_available: CronScheduler가 주입되었으면 True.

    Returns:
        LLM에 전달할 ToolDefinition 리스트.
    """
    tools: list[ToolDefinition] = [
        _CLI_TOOL,
        _WEB_FETCH_TOOL,
        _FILE_READ_TOOL,
        _FILE_WRITE_TOOL,
        _FILE_MANAGE_TOOL,
        _SKILL_DOCS_TOOL,
        _SEARCH_MEMORY_TOOL,
        _CLARIFY_TOOL,
    ]

    if cron_available:
        tools.append(_CRON_TOOL)

    # 외부 스킬이 있으면 execute_skill 함수 추가
    if skills:
        skill_names = [s.name for s in skills]
        skill_list_text = ", ".join(skill_names)
        tools.append(
            ToolDefinition(
                name="execute_skill",
                description=(
                    f"사용자가 설치한 외부 스킬을 실행한다. "
                    f"사용 가능한 스킬: {skill_list_text}. "
                    f"스킬을 처음 사용하기 전에 반드시 skill_docs 도구로 사용법을 먼저 확인할 것."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": f"실행할 스킬 이름. 가능한 값: {skill_list_text}",
                        },
                        "command": {
                            "type": "string",
                            "description": "스킬의 SKILL.md에 명시된 실행 명령어 (전체 셸 명령)",
                        },
                        "args": {
                            "type": "string",
                            "description": "스킬에 전달할 인자 (공백 구분)",
                        },
                    },
                    "required": ["skill_name"],
                },
            )
        )

    return tools
