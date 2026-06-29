"""내장 도구 및 외부 스킬의 Function Calling 스키마 레지스트리.

Native Function Calling에 사용할 ToolDefinition 목록을 조립한다.
내장 도구 스키마를 NativeToolSpec registry로 관리하고, 외부 스킬을 위한
execute_skill 함수 1종을 동적으로 정의한다.

설계 결정:
  - 외부 스킬은 개별 함수로 등록하지 않고, 단일 execute_skill 함수에
    skill_name 인자로 디스패치 (Hermes Agent 방식). 스킬 추가/삭제 시
    스키마 재생성이 불필요하여 hot-reload와 호환됨.
  - 도구 이름은 API 호환성을 위해 언더스코어 사용 (web_fetch 등).
    orchestrator에서 핸들러 매핑 시 이 이름을 그대로 사용하며,
    registry/dispatch 검증으로 drift를 부팅 시점에 잡는다.
  - operator/development scope 도구는 operator_gate가 열릴 때만 노출한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from simpleclaw.llm.models import ToolDefinition
from simpleclaw.skills.models import SkillDefinition


class ToolScope(str, Enum):
    """Native tool이 노출될 수 있는 실행 context 범위."""

    RUNTIME = "runtime"
    OPERATOR = "operator"
    DEVELOPMENT = "development"


class ToolRisk(str, Enum):
    """운영자 gate 판단에 쓰는 Native tool 위험도 metadata."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class NativeToolSpec:
    """ToolDefinition에 scope/risk/operator gate metadata를 붙인 registry 항목."""

    definition: ToolDefinition
    scope: ToolScope = ToolScope.RUNTIME
    risk: ToolRisk = ToolRisk.LOW
    operator_gate_required: bool = False
    aliases: tuple[str, ...] = ()


DEFAULT_TOOL_SCOPES = (ToolScope.RUNTIME,)


# ---------------------------------------------------------------------------
# 내장 도구 스키마 정의 (8종)
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

_WEB_SEARCH_TOOL = ToolDefinition(
    name="web_search",
    description="일반 질의어로 웹 검색을 수행해 후보 URL 목록을 찾는다. "
                "결과는 제목, URL, 짧은 snippet/source만 포함하며 상세 본문은 "
                "필요한 URL을 골라 web_fetch로 가져온다. 최신 뉴스, 기업/시장 이슈처럼 "
                "URL을 모르는 실시간 정보 탐색에 사용한다.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색할 질의어",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "반환할 검색 결과 개수 (1~10, 기본값: 5)",
            },
        },
        "required": ["query"],
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
                "반복 또는 one-shot 여부는 run_once/max_runs/expires_at metadata로 명시한다. "
                "cron 실행 컨텍스트 안에서는 list 외 mutation(add/remove/enable/disable)이 허용되지 않는다.",
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
            "run_once": {
                "type": "boolean",
                "description": "True이면 한 번 실행 후 자동 비활성/정리되는 one-shot 작업으로 등록한다.",
            },
            "max_runs": {
                "type": "integer",
                "minimum": 1,
                "description": "최대 실행 횟수. run_once=True이면 반드시 1이어야 하며 생략 시 1로 정규화된다.",
            },
            "expires_at": {
                "type": "string",
                "description": "작업 만료 시각 ISO-8601 문자열. 현재 시각보다 미래여야 한다.",
            },
        },
        "required": ["cron_action"],
    },
)


_RUNTIME_STATUS_TOOL = ToolDefinition(
    name="runtime_status",
    description=(
        "운영자 전용 read-only 런타임 진단. 프로세스/PID/cwd/HOME, LaunchAgent, "
        "git HEAD, Admin API health, listen port, FD count, scheduler 상태를 요약한다. "
        "재시작/파일 수정/배포 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "include": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "process",
                        "launchd",
                        "git",
                        "health",
                        "ports",
                        "fd",
                        "scheduler",
                    ],
                },
                "description": "포함할 진단 섹션. 생략하면 전체 read-only 섹션을 반환한다.",
            },
            "verbose": {
                "type": "boolean",
                "description": "True이면 ps/lsof 원문을 더 길게 포함한다 (기본값: false).",
            },
        },
        "required": [],
    },
)


_CONFIG_INSPECT_TOOL = ToolDefinition(
    name="config_inspect",
    description=(
        "운영자 전용 read-only effective config 요약. live config path를 명시하고 "
        "llm/agent/memory/skills/recipes/daemon/admin_api/security 섹션을 "
        "시크릿 redaction 및 선택적 경로 절대화와 함께 보여준다. "
        "설정 파일을 쓰거나 재시작/배포 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": [
                    "all",
                    "llm",
                    "agent",
                    "memory",
                    "skills",
                    "recipes",
                    "daemon",
                    "admin_api",
                    "security",
                ],
                "description": "조회할 config 섹션. 생략하면 all.",
            },
            "resolve_paths": {
                "type": "boolean",
                "description": "True이면 *_path/*_dir/*_file 및 path/dir/file 키의 ~를 절대 경로로 확장한다.",
            },
            "redact": {
                "type": "boolean",
                "description": "True이면 token/api_key/master_key 등 실제 시크릿 값을 마스킹한다 (기본값: true).",
            },
        },
        "required": [],
    },
)


_LOG_DEBUG_TOOL = ToolDefinition(
    name="log_debug",
    description=(
        "운영자 전용 read-only 로그 진단. 최근 bot.log tail, ERROR/Traceback, "
        "trace_id 주변, tool loop/recipe/skill/Telegram/Admin API/scheduler 관련 줄을 "
        "시크릿 redaction과 줄 수 제한을 적용해 반환한다. 로그 파일을 쓰거나 "
        "프로세스 재시작 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "recent",
                    "errors",
                    "trace",
                    "tool_loop",
                    "recipe",
                    "skill",
                    "telegram",
                    "admin_api",
                    "scheduler",
                ],
                "description": "조회할 로그 관점. 생략하면 recent.",
            },
            "lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "반환할 최대 줄 수. 1~200으로 제한되며 기본값은 80.",
            },
            "pattern": {
                "type": "string",
                "description": "추가 부분 문자열 필터. 정규식으로 실행하지 않고 대소문자 무시 contains로만 사용한다.",
            },
            "trace_id": {
                "type": "string",
                "description": "특정 trace_id가 포함된 줄만 조회한다. action=trace와 함께 쓰면 한 요청 흐름을 좁힐 수 있다.",
            },
        },
        "required": [],
    },
)


_ASSET_INVENTORY_TOOL = ToolDefinition(
    name="asset_inventory",
    description=(
        "운영자 전용 read-only asset inventory. native tool registry, "
        "SimpleClaw runtime skills, recipes, MCP server/tool 상태, selector config를 "
        "source/path/error metadata와 함께 요약한다. Hermes skill과 런타임 skill을 "
        "구분하기 위해 source를 명시하며 파일 수정/재시작 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["all", "native_tools", "skills", "recipes", "mcp", "selector"],
                "description": "조회할 asset 범위. 생략하면 all.",
            },
            "include_paths": {
                "type": "boolean",
                "description": "True이면 skill_dir/recipe path/MCP server config path성 metadata를 포함한다.",
            },
            "include_errors": {
                "type": "boolean",
                "description": "True이면 recipe/config parse error와 discovery failure 요약을 포함한다.",
            },
        },
        "required": [],
    },
)


_DEPLOY_STATUS_TOOL = ToolDefinition(
    name="deploy_status",
    description=(
        "운영자 전용 read-only 배포 상태 진단. live checkout의 branch/HEAD, "
        "origin/main 또는 origin/dev ahead/behind, dirty paths와 deploy range overlap, "
        "origin/main..origin/dev unreleased commit, open PR 요약을 보여준다. "
        "pull/merge/restart/deploy 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "compare": {
                "type": "string",
                "enum": ["main", "dev"],
                "description": "HEAD와 비교할 origin branch. 생략하면 main.",
            },
            "include_prs": {
                "type": "boolean",
                "description": "True이면 gh pr list로 open PR 요약을 포함한다 (gh 실패 시 git-only fallback).",
            },
        },
        "required": [],
    },
)


_RECIPE_VALIDATE_TOOL = ToolDefinition(
    name="recipe_validate",
    description=(
        "운영자/개발 전용 read-only recipe.yaml 검증. configured recipes.dir 기준으로 "
        "name 또는 path를 resolve해 YAML parse, 필수 필드, empty/provided params 렌더 "
        "sanity, slash command 충돌 warning을 반환한다. live recipe 파일을 쓰거나 "
        "실행/재시작/배포 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "recipes.dir 아래의 recipe 디렉터리 이름. path가 없을 때 사용한다.",
            },
            "path": {
                "type": "string",
                "description": "recipes.dir 아래 recipe.yaml 또는 recipe.yml 경로. name보다 우선한다.",
            },
            "render_params": {
                "type": "object",
                "description": "렌더 smoke에 사용할 선택적 파라미터 dict. 값은 문자열로 정규화된다.",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": [],
    },
)


_SKILL_VALIDATE_TOOL = ToolDefinition(
    name="skill_validate",
    description=(
        "운영자/개발 전용 read-only runtime skill 검증. configured skills local/global dir에서 "
        "name으로 SKILL.md discovery 결과를 찾고, script_path 추론/스크립트와 venv/python runner "
        "존재 여부를 확인한다. smoke=True일 때만 짧은 --help 실행을 timeout/redaction과 함께 수행한다. "
        "skill 파일을 쓰거나 설치/재시작/배포 같은 side effect는 수행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "검증할 runtime skill 이름. SKILL.md discovery 결과의 name과 정확히 일치해야 한다.",
            },
            "smoke": {
                "type": "boolean",
                "description": "True이면 script를 짧게 실행해 --help smoke를 수행한다. 기본값 false라 side effect가 없다.",
            },
            "command_args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "smoke=True일 때 script에 전달할 args. 생략하면 ['--help']를 사용한다.",
            },
        },
        "required": ["name"],
    },
)


_RESTART_RUNTIME_TOOL = ToolDefinition(
    name="restart_runtime",
    description=(
        "운영자 전용 승인형 런타임 재시작 도구. confirm=true와 reason을 명시한 경우에만 "
        "macOS LaunchAgent kickstart -k를 수행하고, 재시작 후 PID 변경, Admin health, "
        "Telegram/scheduler/dashboard flags, FD count를 검증해 반환한다. "
        "일반 사용자 context에는 노출되지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["launchagent_kickstart"],
                "description": "재시작 방식. 현재는 macOS LaunchAgent kickstart만 지원한다.",
            },
            "confirm": {
                "type": "boolean",
                "description": "True일 때만 실제 restart side effect를 수행한다. 기본/False는 차단된다.",
            },
            "reason": {
                "type": "string",
                "description": "운영자가 승인한 재시작 사유. 결과 JSON과 감사 로그 맥락에 남긴다.",
            },
        },
        "required": ["method", "confirm", "reason"],
    },
)


_STUDY_STATUS_TOOL = ToolDefinition(
    name="study_status",
    description=(
        "운영자 전용 Agent Study Wiki 조회/관리 도구. Agent 가 매일 공부해 쌓은 외부 "
        "세계 배경지식(topic registry + daily study run + study item)을 운영자가 점검하고 "
        "조작한다. action=status 는 최근 study run·active topic·stale topic·low-confidence "
        "item 을 한 번에 보여 주고, topics 는 전체 topic 목록, show 는 단일 topic 과 "
        "overview 페이지를 반환한다. refresh 는 다음 daily run 에서 재수집하도록 요청 "
        "플래그를 걸고, archive 는 잘못 공부한 topic 을 비활성화한다. 일반 사용자에게는 "
        "노출되지 않으며, 동기적으로 무거운 수집을 실행하지 않는다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "topics", "show", "refresh", "archive"],
                "description": "수행할 동작. 생략하면 status.",
            },
            "topic_id": {
                "type": "string",
                "description": "show/refresh/archive 대상 topic id. 해당 action 에 필수.",
            },
            "include_archived": {
                "type": "boolean",
                "description": "action=topics 일 때 archived topic 도 포함할지 여부. 기본 false.",
            },
            "low_confidence_threshold": {
                "type": "number",
                "description": "low-confidence 판정 임계값 override (0~1). 생략하면 config 기본값.",
            },
        },
        "required": [],
    },
)


_SKILL_LEARNING_TOOL = ToolDefinition(
    name="skill_learning",
    description=(
        "운영자 전용 skill learning review 도구. 성공한 복잡 tool trace에서 생성된 "
        "pending skill 후보를 list/show/accept/reject/materialize 한다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "show", "accept", "reject", "materialize"]},
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "all"]},
            "reason": {"type": "string"},
            "target_dir": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["action"],
    },
)


_NATIVE_TOOL_SPECS: tuple[NativeToolSpec, ...] = (
    NativeToolSpec(_CLI_TOOL, risk=ToolRisk.MEDIUM),
    NativeToolSpec(_WEB_FETCH_TOOL),
    NativeToolSpec(_WEB_SEARCH_TOOL),
    NativeToolSpec(_FILE_READ_TOOL),
    NativeToolSpec(_FILE_WRITE_TOOL, risk=ToolRisk.MEDIUM),
    NativeToolSpec(_FILE_MANAGE_TOOL, risk=ToolRisk.MEDIUM),
    NativeToolSpec(_SKILL_DOCS_TOOL),
    NativeToolSpec(_SEARCH_MEMORY_TOOL),
    NativeToolSpec(_CLARIFY_TOOL),
    NativeToolSpec(_CRON_TOOL, risk=ToolRisk.MEDIUM),
    NativeToolSpec(
        _RUNTIME_STATUS_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _CONFIG_INSPECT_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _LOG_DEBUG_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _ASSET_INVENTORY_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _DEPLOY_STATUS_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _RECIPE_VALIDATE_TOOL,
        scope=ToolScope.DEVELOPMENT,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _SKILL_VALIDATE_TOOL,
        scope=ToolScope.DEVELOPMENT,
        risk=ToolRisk.LOW,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _RESTART_RUNTIME_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.HIGH,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        _SKILL_LEARNING_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.HIGH,
        operator_gate_required=True,
    ),
    NativeToolSpec(
        # refresh/archive 가 topics.yaml 을 변경하므로 MEDIUM risk.
        _STUDY_STATUS_TOOL,
        scope=ToolScope.OPERATOR,
        risk=ToolRisk.MEDIUM,
        operator_gate_required=True,
    ),
)


def _normalize_scopes(scopes: Iterable[ToolScope | str]) -> frozenset[ToolScope]:
    """호출자가 문자열/Enum 중 무엇을 넘겨도 ToolScope 집합으로 정규화한다."""
    return frozenset(ToolScope(scope) for scope in scopes)


def build_native_tool_registry(
    *,
    cron_available: bool = False,
    scopes: Iterable[ToolScope | str] = DEFAULT_TOOL_SCOPES,
    operator_gate: bool = False,
    extra_specs: Iterable[NativeToolSpec] = (),
) -> tuple[NativeToolSpec, ...]:
    """현재 context에 노출 가능한 native tool registry 항목을 반환한다.

    Args:
        cron_available: CronScheduler가 주입되었으면 True. False면 cron spec을 제외한다.
        scopes: 노출을 허용할 scope 목록. 기본값은 일반 사용자 runtime만 허용한다.
        operator_gate: True일 때만 operator/development gated spec이 노출된다.
        extra_specs: 후속 운영 도구 이슈가 추가 spec을 주입해 검증할 수 있는 확장점.

    Returns:
        scope/gate/cron 조건을 통과한 NativeToolSpec 튜플.
    """
    allowed_scopes = _normalize_scopes(scopes)
    specs: list[NativeToolSpec] = []
    for spec in (*_NATIVE_TOOL_SPECS, *tuple(extra_specs)):
        if spec.definition.name == "cron" and not cron_available:
            continue
        if spec.scope not in allowed_scopes:
            continue
        if spec.operator_gate_required and not operator_gate:
            continue
        specs.append(spec)
    return tuple(specs)


def native_tool_names(
    *,
    cron_available: bool = False,
    scopes: Iterable[ToolScope | str] = DEFAULT_TOOL_SCOPES,
    operator_gate: bool = False,
    extra_specs: Iterable[NativeToolSpec] = (),
) -> frozenset[str]:
    """registry 조건을 통과한 native tool function-call 이름 집합을 반환한다."""
    return frozenset(
        spec.definition.name
        for spec in build_native_tool_registry(
            cron_available=cron_available,
            scopes=scopes,
            operator_gate=operator_gate,
            extra_specs=extra_specs,
        )
    )


def validate_dispatch_tool_names(
    dispatch_names: Iterable[str],
    *,
    scopes: Iterable[ToolScope | str] = DEFAULT_TOOL_SCOPES,
    operator_gate: bool = False,
) -> None:
    """dispatch mapping이 native registry와 일치하는지 검증한다.

    execute_skill은 외부 스킬 동적 도구라 이 static native registry 검증 대상이
    아니다. 후속 operator/development 도구는 registry에 추가하면서 이 검증의
    기준 집합도 자연스럽게 넓어진다.
    """
    expected = native_tool_names(
        cron_available=True,
        scopes=scopes,
        operator_gate=operator_gate,
    )
    actual = frozenset(dispatch_names)
    missing = expected - actual
    unknown = actual - expected
    if missing or unknown:
        raise ValueError(
            "native tool dispatch mismatch: "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


# ---------------------------------------------------------------------------
# 빌더 함수
# ---------------------------------------------------------------------------

def build_tool_definitions(
    skills: list[SkillDefinition],
    cron_available: bool = False,
    scopes: Iterable[ToolScope | str] = DEFAULT_TOOL_SCOPES,
    operator_gate: bool = False,
) -> list[ToolDefinition]:
    """현재 상태에 맞는 ToolDefinition 목록을 조립한다.

    Args:
        skills: 등록된 외부 스킬 목록.
        cron_available: CronScheduler가 주입되었으면 True.
        scopes: 노출할 native tool scope 목록. 기본값은 runtime만 허용한다.
        operator_gate: operator/development scope 노출을 허가하는 명시 gate.

    Returns:
        LLM에 전달할 ToolDefinition 리스트.
    """
    tools: list[ToolDefinition] = [
        spec.definition
        for spec in build_native_tool_registry(
            cron_available=cron_available,
            scopes=scopes,
            operator_gate=operator_gate,
        )
    ]

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
