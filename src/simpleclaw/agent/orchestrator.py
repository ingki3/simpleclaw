"""Agent orchestrator — 페르소나·스킬·메모리·LLM을 하나로 묶는 중앙 조율기.

응답 파이프라인 (Native Function Calling):
1. 사용자 메시지 수신
2. LLM에 도구 정의(tools)와 함께 메시지 전송
3. LLM이 tool_calls를 반환하면 → 도구 실행 → 결과를 메시지에 추가 → 재호출
4. LLM이 텍스트만 반환하면 → 최종 응답으로 반환

Hot-reload 정책:
  AGENT.md, USER.md, MEMORY.md, 스킬/레시피 파일은 매 메시지(process_message /
  process_cron_message) 진입 시 1회 디스크에서 다시 읽는다.
  → 파일 수정 후 봇 리스타트 없이 다음 메시지부터 반영됨.
  → tool loop 내부에서는 캐시된 값을 재사용하여 불필요한 I/O를 방지함.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.config import (
    load_agent_config,
    load_memory_config,
    load_persona_config,
    load_recipes_config,
)
from simpleclaw.llm.models import LLMRequest, SystemBlock, ToolCall
from simpleclaw.llm.router import create_router
from simpleclaw.logging.trace_context import trace_scope
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.embedding_service import EmbeddingService
from simpleclaw.memory.models import (
    CHANNEL_CRON_ADMIN,
    CHANNEL_RECIPE_PREFIX,
    ConversationMessage,
    MessageRole,
)
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.security import (
    CommandGuard,
    DangerousCommandError,
    filter_env,
    get_preexec_fn,
    kill_process_group,
)
from simpleclaw.security.sanitize import (
    sanitize_tool_error,
    sanitize_tool_output,
)
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.models import SkillDefinition

from simpleclaw.agent.builtin_tools import (
    handle_clarify,
    handle_cron_action,
    handle_file_manage,
    handle_file_read,
    handle_file_write,
    handle_skill_docs,
    handle_web_fetch,
)
from simpleclaw.agent.clarify import ClarifyRequest, clarify_chat_id_var
from simpleclaw.agent.commands import try_cron_command, try_recipe_command
from simpleclaw.agent.file_mutation_tracker import (
    FileMutationTracker,
    TrackedRoot,
    format_footer,
)
from simpleclaw.agent.tool_schemas import build_tool_definitions

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler
    from simpleclaw.logging.metrics import MetricsCollector
    from simpleclaw.logging.structured_logger import StructuredLogger

logger = logging.getLogger(__name__)

# 시스템 프롬프트에 추가할 도구 사용 안내 (ReAct 형식 대신 간결한 지시).
#
# BIZ-171 — 각 가드는 별도 const 로 선언하고 ``"\n".join`` 으로 합친다.
# 새 가드를 추가할 때는 ``_GUARD_X`` 상수 한 줄 + 아래 join 리스트 한 줄만
# 수정한다. 이전에는 트리플-쿼트 한 줄 옆으로 두 PR 이 동시에 끼어들어 git
# 자동 머지가 실패했음 (2026-05-12: BIZ-164 #151 ↔ BIZ-166 #152, 별도 릴리스
# release/2026-05-12e #155 강제).
_BASE_INSTRUCTION = (
    "You have access to tools. Use them when you need real-time information or "
    "to perform actions. Do NOT fabricate information — always use a tool to verify.\n"
    "When the user asks about real-time data (calendar, news, stocks, weather, etc.), "
    "you MUST use the appropriate tool. Never answer from memory for such questions."
)

_GUARD_SKILL_DOCS_FIRST = (
    "Before using a user-installed skill for the first time, "
    "call skill_docs to read its usage."
)

# BIZ-166 — 사용자 설치 스킬을 ``uvx``/``pipx run`` 으로 호출하려다 실패하는
# 패턴 차단. 라우팅 자체는 executor 가 막지만, 이 한 줄은 모델이 첫 시도부터
# ``execute_skill`` 을 고르도록 유도한다.
_GUARD_SKILL_DISPATCH = (
    "User-installed skills run from local venvs, NOT from a package registry. "
    "Never call them with `uvx <skill-name>` or `pipx run <skill-name>` — those forms "
    "always fail. Use `execute_skill(skill_name=..., args=...)` and let the runtime "
    "resolve the venv path for you."
)

# BIZ-164 — 작은 모델이 과거 대화에 남은 실패 도구 호출(예: 5/10 의
# ``link-git-summarizer``) 흔적을 보고 새 사용자 메시지에서도 같은 도구를
# 다시 시도하는 패턴(2026-05-12 17:46 "오늘 롯데 선발투수" 사고)을 줄이기 위한
# 프롬프트 가드. 도구 라우팅 자체는 ``_tool_loop`` 의 history 필터(#2)가 끊고,
# 이 한 줄은 그 필터를 빠져나가는 텍스트 흔적까지 모델이 무시하게 보강한다.
_GUARD_PRIOR_TURN_FAILURE = (
    "Do not re-run a skill that you saw fail in a prior turn — "
    "those traces belong to a previous, unrelated request."
)

_GUARD_LANGUAGE = "Respond in the same language as the user."

_GUARD_OPEN_COMMAND = (
    "NEVER use the `open` command. This agent runs in a headless environment."
)

# BIZ-167 — 모델이 페이지 본문 회수에 ``execute_skill agent-browser open ... &&
# agent-browser wait --load networkidle && agent-browser text`` composite 명령을
# 첫 시도로 골라 ``networkidle`` 이 settle 하지 않는 SPA(wikidocs.net 등)에서
# 60초 skill timeout 을 통째로 소진하는 사고 다발(2026-05-12). 같은 일을 하는
# 내장 ``web_fetch`` 는 정적 fetch + 헤드리스 자동 폴백을 8초 ``load`` wait 로
# 묶고 부분 결과라도 반환하므로, 본문 읽기는 무조건 ``web_fetch`` 가 정답.
# ``agent-browser`` 는 클릭/폼/스크린샷처럼 상호작용이 필요한 경우에만.
#
# BIZ-187 follow-up — agent-browser 가 정말 필요할 때조차도 ``open && wait && text``
# 를 한 줄 composite 로 묶어 보내면 SPA 에서 단일 호출이 60s 를 넘기고 (max 180s
# 로 늘렸어도) 모델이 한 turn 안에 결과를 못 보고 또 같은 chain 으로 재시도하면서
# tool loop 가 죽는다. 단계별로 turn 을 분리하면 각 단일 명령은 안정 구간 안에
# 끝난다 — 이 가이드를 명시적으로 박아 둠.
_GUARD_WEB_FETCH_PREFERRED = (
    "To read page text (articles, blogs, search results, docs), use the "
    "`web_fetch` tool — it auto-falls back to a headless browser when needed. "
    "Do NOT compose `execute_skill agent-browser open ... && wait ... && text` "
    "commands for plain text retrieval; reserve `agent-browser` for interactive "
    "tasks (clicks, form fills, screenshots). When you do call agent-browser, "
    "use `wait --load load` — `networkidle` rarely settles on modern SPAs and "
    "wastes the entire skill timeout. Also issue each agent-browser step as its "
    "own tool call (open → wait → text/snapshot in separate turns) instead of "
    "chaining them with `&&`; chained chains amplify single-step timeouts and "
    "exhaust the tool loop on SPA sites. "
    # BIZ-190 — wikidocs.net / npmjs.com 같이 Cloudflare/anti-bot 가드를 띄우는
    # 사이트에서 web_fetch 가 짧은 본문(예: 27자, 202자) 이나 ``FETCH_BLOCKED:``
    # 마커를 돌려주면, 같은 URL 을 agent-browser/cli/skill 로 재시도하지 말 것.
    # web_fetch 는 이미 정적 + 헤드리스 두 경로를 시도한 결과이므로 추가 우회는
    # 무의미하고 tool loop 만 소진한다. 사용자에게 "사이트가 자동 회수를 차단함"
    # 으로 보고하고 종료한다.
    "If `web_fetch` returns a short body or a `FETCH_BLOCKED:` marker for a URL, "
    "the site is blocking automated fetching — `web_fetch` has already tried "
    "both static and headless paths. Do NOT retry the same URL via "
    "`agent-browser`, `cli`, or any other skill; reply to the user that the "
    "page cannot be retrieved automatically and offer a graceful alternative "
    "(ask for the text directly, summarize from prior knowledge, etc.)."
)

# BIZ-260 — clarify 다지선다 도구 사용 가이드. 사용자가 명확하지 않은 의도
# (예: 후보가 여러 개인 메일/캘린더/파일/주식 종목 선택) 를 보일 때 LLM 이
# clarify 도구를 잡도록 유도. 자유형 질문(이름·주제 등) 에는 평문 응답이 더
# 자연스럽다.
_GUARD_CLARIFY_TOOL = (
    "When the user's request has multiple short, enumerable candidate answers "
    "(which email/event/file/ticker to act on), call `clarify(question, options)` "
    "instead of asking in plain text. On channels that support it, the options "
    "render as tap buttons. Calling clarify ends the turn — do NOT also send a "
    "text response in the same turn."
)

_TOOL_USAGE_INSTRUCTION = "\n".join(
    [
        _BASE_INSTRUCTION,
        _GUARD_SKILL_DOCS_FIRST,
        _GUARD_SKILL_DISPATCH,
        _GUARD_PRIOR_TURN_FAILURE,
        _GUARD_LANGUAGE,
        _GUARD_OPEN_COMMAND,
        _GUARD_WEB_FETCH_PREFERRED,
        _GUARD_CLARIFY_TOOL,
    ]
)

# BIZ-160 — tool 루프가 max_tool_iterations 를 다 쓰고도 LLM 이 빈 텍스트를 돌려준
# 사고(2026-05-08)에서 사용자에게 아무 메시지도 가지 않아 봇이 죽은 것처럼 보였음.
# 빈 응답 자리에 안내 메시지를 채워, 채널 라우터(`if response:`)가 sendMessage 를
# skip 하지 않도록 한다.
_BUDGET_EXHAUSTED_EMPTY_MESSAGE = (
    "여러 도구를 시도했지만 답을 마무리하지 못했습니다.\n"
    "질문을 짧게 다시 표현해 주시거나, URL/파일 경로를 함께 알려 주시면 도움이 됩니다.\n"
    "(debug: tool loop {iterations}회 반복 후 종료)"
)
_BUDGET_EXHAUSTED_HINT_SUFFIX = (
    "(참고: 도구 호출 한도 {iterations}회에 도달해 추가 정보 수집을 멈췄습니다)"
)

# BIZ-141 — forced final-answer LLM 호출이 provider 측에서 hang 하면 메시지가
# 영구 침묵하는 사고를 막기 위한 hard timeout. 일반 응답 시간(통상 1~3초) 대비
# 충분히 길고, hang 식별엔 충분히 짧은 경험적 컷.
_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS = 30.0
_FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE = (
    "응답이 지연되어 처리를 종료했습니다. 죄송하지만 한 번 더 말씀해 주세요. "
    "(debug: final-answer LLM 호출이 {timeout:.0f}초 안에 응답하지 않음)"
)

# BIZ-190 — ``agent-browser`` composite chain (``open && wait && text|evaluate``)
# 은 BIZ-187 에서 시스템 프롬프트 가드 + 180s 화이트리스트 타임아웃으로 봉합을
# 시도했지만, 작은 모델(gemini-2.5-flash-lite 등)이 가드 문구를 무시하고 첫 시도
# 부터 같은 chain 을 다시 보내는 패턴이 잔존(2026-05-13 20:19~20:36 KST 시드
# 측정 4건). 가드를 텍스트로만 두면 한 번 잘못된 시도를 못 막고 그 결과가 다시
# tool history 에 누적돼 후속 turn 까지 같은 chain 을 유도한다. 실행 직전에
# subprocess 전에서 차단하고 명확한 단일-호출 안내를 tool result 로 돌려 줌으로
# 써 LLM 이 같은 turn 안에서 정정하도록 한다.
_AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE = (
    "Error: composite `agent-browser` chains are blocked. Each agent-browser "
    "step must be a SEPARATE tool call (one `execute_skill` per `open`, `wait`, "
    "`get`/`evaluate` step). For plain page text, prefer `web_fetch` — it already "
    "auto-falls back to a headless browser. If `web_fetch` returned a short body "
    "for this URL, the site is blocking automated fetching; do NOT keep trying "
    "the same URL via agent-browser. Reply to the user that the page cannot be "
    "retrieved instead."
)

# BIZ-190 — 같은 URL 에 대해 ``agent-browser open`` 류 호출을 한 turn 안에서
# 반복하는 패턴(시드 측정 seed-2/3/8/9 의 4건 공통) 의 cap. 첫 시도가 daemon
# busy(os error 35) 등으로 실패하면 LLM 이 같은 명령을 재시도하면서 max-iter
# 까지 누적 소진한다. 첫 호출 1회만 허용하고 두 번째부터는 합성 응답으로
# 즉시 종결.
_AGENT_BROWSER_PER_TURN_CALL_CAP = 2

_AGENT_BROWSER_CAP_EXCEEDED_MESSAGE = (
    "Error: `agent-browser` has already been attempted {count} times in this "
    "turn and is being rate-limited to avoid exhausting the tool loop. If the "
    "page text could not be retrieved by `web_fetch` (which already includes a "
    "headless fallback), the site is blocking automated fetching. Reply to the "
    "user that the page cannot be retrieved rather than retrying with "
    "agent-browser, cli, or another skill."
)

# BIZ-251 — verifier footer 가 "변경 없음" 마커를 명시적으로 부착해야 하는
# tool 이름. 이들 도구는 디스크/외부 상태를 바꿀 *수* 있으므로, 호출 직후
# diff 가 비었다는 사실 자체가 LLM 의 silent-fail/환각 인지에 가치가 있다.
# read-only 도구(web_fetch, skill_docs, cron list, file_read) 는 빈 diff 가
# 정상 경로이므로 footer 를 생략해 토큰을 절약한다.
_FILE_MUTATING_TOOLS = frozenset(
    {"file_write", "file_manage", "execute_skill", "cli"}
)


class AgentOrchestrator:
    """페르소나 + 스킬 + 대화 이력 + LLM을 조합하는 중앙 오케스트레이터.

    응답 파이프라인 (Native Function Calling):
    1. 시스템 프롬프트 조립 (페르소나 + 스킬 개요 + 도구 사용 안내)
    2. 도구 정의를 LLM API의 tools 파라미터로 전달
    3. LLM이 tool_calls 반환 시 → 도구 실행 → 결과를 메시지에 추가 → 재호출
    4. LLM이 텍스트만 반환 시 → 최종 응답
    5. 대화 저장
    """

    def __init__(
        self,
        config_path: str | Path = "config.yaml",
        *,
        metrics: MetricsCollector | None = None,
        structured_logger: StructuredLogger | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        # 메트릭 수집기 — 서브프로세스 종료 결과를 누적하여 누수 추세를 모니터링.
        # None이면 메트릭이 기록되지 않으며, 기존 동작과 호환된다.
        self._metrics = metrics
        # 구조화 로거 — RAG 회상(action_type="rag_retrieve")과 같은 관찰 가능성 이벤트를 적재.
        # None이면 로그가 비활성화되며, 기존 동작과 호환된다.
        self._structured_logger = structured_logger

        # --- 정적 설정 로드 (리스타트 시에만 갱신) ---
        agent_config = load_agent_config(config_path)
        persona_config = load_persona_config(config_path)
        recipes_config = load_recipes_config(config_path)

        # BIZ-202: 봇이 채팅에서 만든 레시피와 데몬이 cron 으로 로드하는 레시피가
        # 같은 절대 경로를 보도록 config 한 곳에서 결정. 기본은 ``~/.simpleclaw/recipes``
        # — 봇 워크스페이스(`~/.simpleclaw/workspace`) 의 sandbox-write 허용 트리 안에
        # 들어가야 봇 `cli`/`file_write` 도구가 직접 쓸 수 있다.
        self._recipes_dir = str(
            Path(recipes_config["dir"]).expanduser()
        )
        # 디렉터리는 부팅 시 자동 생성 — 없으면 봇이 새 레시피 작성을 시도하기 전에
        # mkdir 도구를 명령 받아야 하는 흐름이 되어 사용자 흐름이 깨진다.
        Path(self._recipes_dir).mkdir(parents=True, exist_ok=True)

        self._history_limit = agent_config["history_limit"]

        # 페르소나·스킬 설정값 보관 — _reload_dynamic_files()에서 참조
        self._persona_config = persona_config
        skills_config = self._load_skills_config()
        self._skills_config = skills_config

        # Cron scheduler — build_tool_definitions에서 참조하므로 리로드 전에 초기화
        self._cron_scheduler: CronScheduler | None = None

        # 초기 로드: 페르소나·스킬 파일을 디스크에서 읽어 캐시 필드 채움
        self._reload_dynamic_files()

        # LLM router
        self._router = create_router(config_path)

        # Conversation store
        # BIZ-133: db_path 가 ``~/.simpleclaw/...`` 형태로 오므로 expanduser 로 풀어준다.
        db_path = Path(agent_config["db_path"]).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._store = ConversationStore(db_path)

        # 시맨틱 메모리(RAG, spec 005 Phase 2) 설정 로드
        # enabled=False가 기본 — sentence-transformers 미설치 환경에서도 무난하게 동작
        memory_config = load_memory_config(config_path)
        rag_cfg = memory_config["rag"]
        self._rag_enabled: bool = bool(rag_cfg["enabled"])
        self._rag_top_k: int = int(rag_cfg["top_k"])
        self._rag_threshold: float = float(rag_cfg["similarity_threshold"])
        self._embedding_service: EmbeddingService | None = (
            EmbeddingService(
                model_name=str(rag_cfg["model"]),
                enabled=self._rag_enabled,
            )
            if self._rag_enabled
            else None
        )
        # 백그라운드 임베딩 태스크 강한 참조 — GC로 인한 task drop 방지
        self._background_tasks: set = set()

        # Skill execution timeout
        self._skill_timeout = skills_config.get("execution_timeout", 60)

        # Security: command guard + env filtering
        security_config = self._load_security_config()
        guard_config = security_config.get("command_guard", {})
        self._command_guard = CommandGuard(
            allowlist=guard_config.get("allowlist", []),
            enabled=guard_config.get("enabled", True),
        )
        self._env_passthrough = security_config.get("env_passthrough", [])

        # Multi-turn tool execution budget
        self._max_tool_iterations = agent_config.get("max_tool_iterations", 15)

        # Workspace directory for skill file output.
        # BIZ-133: 기본 위치는 운영 디렉터리(`~/.simpleclaw/workspace`) — 저장소
        # working tree 안에 임시 파일이 쌓이지 않도록.
        self._workspace_dir = Path(
            agent_config.get("workspace_dir", "~/.simpleclaw/workspace")
        ).expanduser()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        # BIZ-162: web_fetch 의 헤드리스 폴백이 nohup PATH 축소 환경에서도 동작하도록
        # 운영자 명시 경로를 config 에서 읽어 핸들러에 주입한다. None 이면 builtin_tools
        # 의 ``_resolve_agent_browser`` 가 PATH + 알려진 후보 경로 자동 탐색.
        web_fetch_cfg = agent_config.get("web_fetch", {}) or {}
        self._headless_binary: str | None = web_fetch_cfg.get("headless_binary")

        # BIZ-187: agent-browser composite chain (예: ``agent-browser open ... &&
        # agent-browser wait --load load && agent-browser text``) 은 SPA(wikidocs.net,
        # npmjs.com 등)에서 60s 의 기본 ``skills.execution_timeout`` 을 정기적으로
        # 넘어 ``Skill command timed out`` 으로 죽고, 모델이 tool loop 안에서 같은
        # composite 를 재시도하면서 ``max_tool_iterations`` 까지 누적 소진되는
        # 사고 다발(2026-05-13 BIZ-182 / BIZ-183 시드 측정). composite 한 호출의
        # 실제 wall time 은 보통 60~120s 이므로 ``agent-browser`` 명령에만 별도의
        # 더 긴 타임아웃을 화이트리스트로 적용한다. 기본 180s 는 시드 측정에서
        # 관찰된 최악(SPA 5건) 의 약 1.5배. None 으로 두면 기본 60s 유지.
        self._agent_browser_timeout: int = int(
            web_fetch_cfg.get("agent_browser_command_timeout", 180)
        )

        # BIZ-251: per-turn file mutation verifier footer.
        # 워크스페이스는 재귀 walk, 페르소나 dir 은 명시 파일 화이트리스트
        # (AGENT.md / USER.md / MEMORY.md) 만 추적해 SQLite/dreaming 부산물이
        # footer 노이즈로 새는 것을 차단한다. ``~/.simpleclaw/`` 가 persona
        # local_dir 인 BIZ-133 경로 가정 — 화이트리스트면 overlap 도 안전.
        persona_local = Path(
            self._persona_config["local_dir"]
        ).expanduser()
        persona_filenames = tuple(
            f["name"] for f in self._persona_config["files"] if "name" in f
        )
        self._mutation_tracker = FileMutationTracker(
            [
                TrackedRoot(".agent/workspace", self._workspace_dir),
                TrackedRoot(".agent", persona_local, files=persona_filenames),
            ]
        )

        # BIZ-260 — clarify 도구의 pending 요청 레지스트리. chat_id → ClarifyRequest.
        # ``_dispatch_tool_call`` 이 채워 넣고, 채널이 ``pop_pending_clarify`` 로 회수.
        # 동일 chat 안에서는 한 번에 하나만 대기 — 새 clarify 가 호출되면 덮어쓴다.
        self._pending_clarify: dict[int, ClarifyRequest] = {}

        logger.info(
            "AgentOrchestrator initialized: persona=%d chars, skills=%d, backend=%s",
            len(self._persona_prompt),
            len(self._skills),
            self._router.get_default_backend(),
        )

    def _reload_dynamic_files(self) -> None:
        """페르소나·스킬 파일을 디스크에서 다시 읽어 캐시 필드를 갱신한다 (hot-reload).

        호출 시점: __init__() 초기화 + 매 메시지 진입 시 1회.
        tool loop 내부에서는 호출하지 않아 불필요한 I/O를 방지한다.
        """
        # --- 페르소나 리로드 (AGENT.md, USER.md, MEMORY.md) ---
        persona_files = resolve_persona_files(
            local_dir=self._persona_config["local_dir"],
            global_dir=self._persona_config["global_dir"],
        )
        assembly = assemble_prompt(
            persona_files, self._persona_config["token_budget"]
        )
        self._persona_prompt = assembly.assembled_text or ""

        # --- 스킬 리로드 (.agent/skills, ~/.agents/skills) ---
        self._skills = discover_skills(
            local_dir=self._skills_config.get("local_dir", ".agent/skills"),
            global_dir=self._skills_config.get("global_dir", "~/.agents/skills"),
        )
        # 이름 기반 조회용 딕셔너리 (fuzzy match에서도 사용)
        self._skills_by_name = {s.name: s for s in self._skills}
        # 시스템 프롬프트용 스킬 목록
        self._skills_prompt = self._format_skills_for_prompt(self._skills)

    def set_cron_scheduler(self, scheduler: CronScheduler) -> None:
        """CronScheduler를 주입하여 cron 도구를 활성화한다."""
        self._cron_scheduler = scheduler
        logger.info("CronScheduler injected into AgentOrchestrator.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_cron_message(self, text: str) -> str:
        """크론 잡 메시지를 격리된 컨텍스트로 처리한다.

        대화 이력을 불러오지 않고 공유 대화 DB에 메시지를 저장하지 않는다.
        진입점이므로 trace_id를 새로 발급해 호출 체인 전체로 전파한다.
        """
        with trace_scope() as trace_id:
            logger.info("Cron message received: trace_id=%s", trace_id)
            self._reload_dynamic_files()
            return await self._tool_loop(text, isolated=True)

    async def process_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str:
        """수신 메시지를 Native Function Calling 파이프라인으로 처리한다.

        진입점이므로 trace_id를 새로 발급해 ``contextvars``로 호출 체인
        (도구 실행, RAG 회상, 백그라운드 임베딩, 서브에이전트/스킬 등) 전체에
        전파한다. ``trace_scope``는 ``with`` 블록 종료 시 이전 trace_id를
        복원하므로 동일 프로세스에서 후속 메시지가 깨끗한 컨텍스트로 시작된다.
        """
        with trace_scope() as trace_id:
            logger.info(
                "Message received: trace_id=%s user=%d chat=%d",
                trace_id, user_id, chat_id,
            )
            self._reload_dynamic_files()

            # BIZ-260 — clarify 도구가 발생시킬 ClarifyRequest 를 chat_id 키로
            # 적재할 수 있도록 contextvar 에 chat_id 를 매단다. tool 핸들러는
            # 자기 시그니처를 바꾸지 않고도 contextvar 로 chat_id 를 얻는다.
            clarify_token = clarify_chat_id_var.set(chat_id)
            try:
                # /cron 명령어 확인
                cron_result = try_cron_command(text, self._cron_scheduler)
                if cron_result is not None:
                    # BIZ-76 — cron 관리 명령(/cron list 등) 응답은 자동 트리거
                    # 카테고리로 묶어 dreaming 의 사용자 관심 추론에서 분리한다.
                    self._save_turn(
                        text, cron_result, channel=CHANNEL_CRON_ADMIN,
                    )
                    return cron_result

                # /recipe-name 명령어 확인 (e.g. /ai-report)
                # BIZ-202: 레시피 디렉터리는 config 기반 — 봇/데몬 양쪽이 같은 절대 경로를 본다.
                recipe_outcome = await try_recipe_command(
                    text, self._tool_loop, recipes_dir=self._recipes_dir,
                )
                if recipe_outcome is not None:
                    recipe_result, recipe_name = recipe_outcome
                    # BIZ-76 — 레시피 산출물은 사용자 발화가 아니라 자동/명령 트리거
                    # 결과이므로 ``recipe:<name>`` 채널로 태깅한다. dreaming 코퍼스
                    # 로더가 이 prefix 를 보고 분리 또는 가중치 다운한다.
                    self._save_turn(
                        text,
                        recipe_result,
                        channel=f"{CHANNEL_RECIPE_PREFIX}{recipe_name}",
                    )
                    return recipe_result

                response_text = await self._tool_loop(text)

                # BIZ-260 — clarify 가 호출됐다면 ``_tool_loop`` 가 빈 텍스트로
                # 종결했을 수 있다. 대화 이력 저장은 항상 "질문 + 번호 옵션"
                # 텍스트로 — 다음 turn 의 LLM 컨텍스트에 옵션이 보존되어 사용자가
                # 텍스트로 "1" / 본문으로 답해도 매칭 가능 (DoD backward compat).
                pending = self._pending_clarify.get(chat_id)
                if pending is not None:
                    response_text = pending.format_user_visible()

                # 일반 사용자 발화는 채널을 명시하지 않는다(=organic). 이후 BIZ-76
                # 후속에서 telegram/webhook/console 같은 origin 메타로 확장될 수 있음.
                self._save_turn(text, response_text)
                return response_text
            finally:
                clarify_chat_id_var.reset(clarify_token)

    # ------------------------------------------------------------------
    # 대화 저장 + 백그라운드 임베딩 (spec 005 Phase 2)
    # ------------------------------------------------------------------

    def _save_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        channel: str | None = None,
    ) -> None:
        """user/assistant 메시지 한 쌍을 저장하고, RAG가 켜져 있으면 임베딩을 백그라운드 부착한다.

        설계 결정:
        - 임베딩은 fire-and-forget 비동기로 처리하여 응답 레이턴시에 영향을 주지 않는다.
        - 동일 턴 내 user → assistant 순서로 저장(시간순 보존).
        - RAG가 비활성이거나 임베딩 서비스가 None이면 저장만 수행한다.

        BIZ-76: ``channel`` 인자가 주어지면 같은 턴의 user/assistant 두 메시지 모두에
        동일 채널을 부착한다. cron-admin / recipe:<name> 같은 자동·명령 트리거 출처를
        이후 dreaming 코퍼스 로더가 분리하거나 가중치 다운하기 위한 메타이다.
        """
        user_id = self._store.add_message(ConversationMessage(
            role=MessageRole.USER, content=user_text, channel=channel,
        ))
        asst_id = self._store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content=assistant_text, channel=channel,
        ))
        self._schedule_embedding(user_id, user_text)
        self._schedule_embedding(asst_id, assistant_text)

    def _schedule_embedding(self, message_id: int, content: str) -> None:
        """주어진 메시지의 임베딩을 백그라운드 태스크로 부착한다.

        실패는 조용히 로그만 남긴다(메시지 자체 저장은 이미 완료되었으므로 RAG만 누락).
        sentence-transformers 모델은 동기 API라 ``asyncio.to_thread``로 워커 스레드에 위임한다.
        """
        if self._embedding_service is None or not self._embedding_service.is_enabled:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 호출 컨텍스트에 이벤트 루프가 없으면 임베딩을 건너뛴다(테스트/동기 호출 보호)
            return

        task = loop.create_task(self._embed_message_async(message_id, content))
        # 강한 참조 유지 — 완료되면 set에서 제거
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _embed_message_async(self, message_id: int, content: str) -> None:
        """메시지를 임베딩하고 ConversationStore에 부착한다(워커 스레드 위임).

        모든 단계는 best-effort이며, 어떤 실패도 호출자로 전파되지 않는다.
        """
        import asyncio
        try:
            assert self._embedding_service is not None  # 호출 전에 확인됨
            vec = await asyncio.to_thread(
                self._embedding_service.encode_passage, content
            )
            if vec is None:
                return
            await asyncio.to_thread(self._store.add_embedding, message_id, vec)
        except Exception as exc:
            logger.warning(
                "Background embedding failed for msg %d: %s", message_id, exc
            )

    # ------------------------------------------------------------------
    # Native Function Calling loop
    # ------------------------------------------------------------------

    async def _tool_loop(
        self, text: str, isolated: bool = False
    ) -> str:
        """Native Function Calling 루프를 실행한다.

        LLM에 도구 정의(tools)와 함께 메시지를 전송하고,
        tool_calls가 반환되면 실행 후 결과를 메시지에 추가하여 재호출한다.
        텍스트만 반환되면 최종 응답으로 반환한다.

        Args:
            text: 사용자 원본 메시지
            isolated: True면 대화 이력 없이 독립 실행 (크론 잡 등)
        """
        # 현재 시각을 KST로 주입
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        datetime_context = now_kst.strftime(
            "[현재 시각: %Y-%m-%d %H:%M (%A) KST]"
        )
        user_content = f"{datetime_context}\n{text}"

        # 메시지 구성
        if isolated:
            messages: list[dict] = [{"role": "user", "content": user_content}]
            rag_context = ""
        else:
            recent = self._store.get_recent(limit=self._history_limit)
            # BIZ-164 — 과거 턴의 ``role=tool`` 메시지와 assistant 메시지의
            # ``tool_calls`` 필드는 다음 턴의 LLM 입력에서 잘라낸다. 5/10 의
            # ``link-git-summarizer`` 같은 실패 도구 호출이 history 에 남아
            # 있으면 작은 모델이 새 사용자 메시지에서도 같은 도구를 다시
            # 시도해 max-iter 까지 낭비하는 사고(2026-05-12 17:46)가 잡힌다.
            # 현재 ``MessageRole`` 은 user/assistant/system 만 정의하므로 실데이터
            # 에선 no-op 이지만, 향후 store 가 tool 역할을 적재하거나 메시지에
            # ``tool_calls`` 속성이 부착되더라도 누설되지 않도록 명시적으로 거른다.
            # 현재 턴 내부(in-flight)의 tool exchange 는 아래 루프에서 그대로
            # 누적되므로 정보 손실 없음.
            messages = []
            for msg in recent:
                role_value = msg.role.value
                if role_value not in ("user", "assistant", "system"):
                    continue
                messages.append({
                    "role": role_value,
                    "content": msg.content,
                })
            messages.append({"role": "user", "content": user_content})
            # 시맨틱 회상: 최근 윈도우에 포함되지 않은 과거 메시지를 추가 컨텍스트로 회수
            recent_contents = {msg.content for msg in recent}
            rag_context = await self._retrieve_relevant_context(
                text, exclude_contents=recent_contents,
            )

        # 시스템 프롬프트는 페르소나/스킬과 RAG 회상 블록을 합친 결과.
        # BIZ-252 — Claude 의 prompt caching 을 위해 세그먼트 단위로도 함께 보낸다.
        # cache 경계: 페르소나 끝 / 스킬 목록 끝. ReAct 지시문과 RAG 블록은 마커 뒤에 둔다.
        system_blocks = self._build_system_blocks(rag_context=rag_context)
        system_prompt = self._flatten_system_blocks(system_blocks)
        tools = build_tool_definitions(
            self._skills,
            cron_available=self._cron_scheduler is not None,
        )

        # BIZ-160 — budget-exhausted 분기에서 운영자가 패턴을 추적할 수 있도록
        # 호출된 도구 이름을 순서대로 누적한다. (logger.warning 으로 박제됨)
        invoked_tool_sequence: list[str] = []

        # BIZ-190 — 같은 turn 안에서 ``agent-browser`` 호출 횟수 카운터. 첫 시도가
        # 실패하면 LLM 이 같은 명령을 cli/execute_skill 채널로 재시도하면서 max-iter
        # 까지 누적 소진하는 패턴(seed-2/3/8/9, 2026-05-13 20:19~20:36 KST) 을 차단.
        # ``_AGENT_BROWSER_PER_TURN_CALL_CAP`` 초과 시 subprocess 진입 전에 합성 응답.
        agent_browser_call_count = 0

        # BIZ-251 — per-turn file mutation verifier footer.
        # 매 iteration 의 tool call 직후, 워크스페이스/페르소나 디스크 상태를
        # diff 하여 마지막 tool result 메시지에 footer 로 부착한다. 다음
        # iteration 의 LLM 입력에서 디스크 사실(SoT) 을 강제 노출함으로써
        # "파일 저장했다" 류 환각과 스킬 silent-fail 을 잡는다.
        prev_snapshot = self._mutation_tracker.snapshot()

        for i in range(self._max_tool_iterations):
            try:
                request = LLMRequest(
                    system_prompt=system_prompt,
                    user_message=user_content,
                    messages=messages,
                    tools=tools,
                    system_blocks=system_blocks,
                )
                response = await self._router.send(request)
            except Exception as exc:
                logger.error("Tool loop LLM error: %s", exc)
                return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

            # tool_calls가 없으면 텍스트 응답 → 최종 답변
            if not response.tool_calls:
                logger.info("Tool loop [%d] final answer: %d chars", i + 1, len(response.text))
                return response.text.strip()

            # tool_calls가 있으면 실행 후 결과를 메시지에 추가
            logger.info(
                "Tool loop [%d] %d tool call(s)",
                i + 1, len(response.tool_calls),
            )

            # assistant 메시지 추가 (tool_calls 포함)
            # _raw_content: Gemini의 thought_signature를 보존하기 위한 원본 Content 객체
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
            if response.raw_assistant_message is not None:
                assistant_msg["_raw_content"] = response.raw_assistant_message
            messages.append(assistant_msg)

            # 각 tool_call 실행 → 결과를 tool 메시지로 추가
            for tc in response.tool_calls:
                invoked_tool_sequence.append(tc.name)
                logger.info("Tool call: %s(%s)", tc.name, json.dumps(tc.arguments, ensure_ascii=False)[:200])

                # BIZ-190: 같은 turn 안에서 ``agent-browser`` 호출 횟수가 cap 을
                # 넘으면 subprocess 진입 전에 합성 응답으로 즉시 종결한다. cap 자체는
                # ``execute_skill`` 의 ``command`` 또는 ``args`` 가 agent-browser 를
                # 호출하는지로 판별 — ``cli`` 도구로 우회 호출하는 경우도 동일하게 적용.
                if self._call_invokes_agent_browser(tc):
                    agent_browser_call_count += 1
                    if agent_browser_call_count > _AGENT_BROWSER_PER_TURN_CALL_CAP:
                        result = _AGENT_BROWSER_CAP_EXCEEDED_MESSAGE.format(
                            count=agent_browser_call_count - 1,
                        )
                        logger.warning(
                            "BIZ-190: agent-browser per-turn cap exceeded "
                            "(%d > %d); synthesizing blocked response",
                            agent_browser_call_count - 1,
                            _AGENT_BROWSER_PER_TURN_CALL_CAP,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": result[:3000],
                        })
                        continue

                result = await self._dispatch_tool_call(tc)
                # PRD §3.5.6 — 다음 턴의 ``role=tool`` 메시지로 들어가기
                # 직전에 구조적 framing 토큰 / 제어문자를 제거한다. 도구
                # 핸들러는 이미 에러 envelope 을 부착해 반환하므로 여기서는
                # 출력 변형(envelope 없음) 만 사용.
                sanitized = sanitize_tool_output(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": sanitized[:3000],
                })
                logger.info("Tool result: %s → %d chars", tc.name, len(sanitized))

            # BIZ-260 — clarify 가 이번 iteration 안에서 호출됐다면 추가 LLM
            # 호출 없이 즉시 종결한다. clarify 는 그 자체로 "사용자에게 되묻기"
            # 의도이므로 다음 도구 호출 / 텍스트 응답이 의미 없다. 반환 텍스트는
            # 빈 문자열 — ``process_message`` 가 ``_pending_clarify`` 에서
            # ``format_user_visible`` 로 다시 조립한다.
            chat_id_for_clarify = clarify_chat_id_var.get()
            if (
                chat_id_for_clarify is not None
                and chat_id_for_clarify in self._pending_clarify
            ):
                logger.info(
                    "Tool loop [%d] terminated by clarify call (chat=%d)",
                    i + 1, chat_id_for_clarify,
                )
                return ""

            # BIZ-251 — verifier footer.
            # iteration 안의 모든 tool call 직후 워크스페이스/페르소나 dir 의
            # 디스크 사실을 캡처해 마지막 tool result 메시지에 부착한다.
            # 다음 iteration 의 LLM 컨텍스트가 이전 step 에서 *실제로*
            # 무엇이 디스크에 쓰였는지를 SoT 로 본다.
            #
            # 변경 없음 + 파일-쓰기 도구 호출이 *있었다* → 명시적 "none"
            # 마커를 부착해 LLM 이 silent-fail/환각을 다음 step 에서 인지
            # 하도록 한다. 어느 쪽도 아니면 footer 를 생략해 토큰을 절약
            # 한다 (DoD: "변경 없음 시 footer 생략").
            try:
                new_snapshot = self._mutation_tracker.snapshot(
                    previous=prev_snapshot,
                )
                file_diff = self._mutation_tracker.diff(
                    prev_snapshot, new_snapshot,
                )
                footer = format_footer(file_diff)
                iteration_had_mutating_call = any(
                    tc.name in _FILE_MUTATING_TOOLS
                    for tc in response.tool_calls
                )
                if not footer and iteration_had_mutating_call:
                    footer = "[file changes this turn: none]"
                if footer and messages and messages[-1].get("role") == "tool":
                    messages[-1]["content"] = (
                        messages[-1]["content"] + "\n\n" + footer
                    )
                prev_snapshot = new_snapshot
            except Exception as exc:  # noqa: BLE001 — verifier 는 best-effort
                # 추적기는 보조 신호이므로 실패해도 turn 을 막지 않는다.
                logger.warning(
                    "FileMutationTracker footer 부착 실패: %s", exc,
                )

        # 예산 소진 — tools=None으로 최종 LLM 호출 (텍스트 강제)
        # BIZ-160 — 사용된 도구 시퀀스를 한 줄로 박제. 운영자가 logs 검색으로
        # 동일 패턴(예: "skill_docs → web_fetch → skill_docs → execute_skill → skill_docs")
        # 을 추적해 max_tool_iterations / 도구 동작을 튜닝할 근거로 사용한다.
        logger.warning(
            "Tool loop max iterations (%d) reached, forcing final answer; "
            "tool_sequence=%s",
            self._max_tool_iterations,
            invoked_tool_sequence,
        )
        try:
            final_request = LLMRequest(
                system_prompt=system_prompt,
                user_message=user_content,
                messages=messages,
                system_blocks=system_blocks,
            )
            # BIZ-141 — provider 측 hang 으로 메시지가 영구 침묵하는 사고를 막는
            # 방어선. 빈 응답(BIZ-160)·예외와 별개로 hang 클래스를 처리.
            final_response = await asyncio.wait_for(
                self._router.send(final_request),
                timeout=_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Tool loop final generation timeout after %ss",
                _FORCED_FINAL_ANSWER_TIMEOUT_SECONDS,
            )
            return _FORCED_FINAL_ANSWER_TIMEOUT_MESSAGE.format(
                timeout=_FORCED_FINAL_ANSWER_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error("Tool loop final generation error: %s", exc)
            return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

        # BIZ-160 — final_response.text 가 빈 문자열이면 채널 라우터의
        # `if response:` 가드가 sendMessage 를 skip 한다. 사용자 채널에
        # 항상 안내가 도달하도록 두 분기로 나눠 빈 응답을 메우거나
        # 의미 있는 응답에 한도 도달 사실을 한 줄 부보한다.
        final_text = (final_response.text or "").strip()
        if not final_text:
            return _BUDGET_EXHAUSTED_EMPTY_MESSAGE.format(
                iterations=self._max_tool_iterations,
            )
        return (
            f"{final_text}\n\n"
            + _BUDGET_EXHAUSTED_HINT_SUFFIX.format(
                iterations=self._max_tool_iterations,
            )
        )

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_tool_call(self, tool_call: ToolCall) -> str:
        """ToolCall을 적절한 핸들러로 라우팅하여 실행 결과를 반환한다."""
        name = tool_call.name
        args = tool_call.arguments

        if name == "execute_skill":
            return await self._dispatch_external_skill(args)
        if name == "cli":
            cmd = args.get("command", "")
            if not cmd:
                return "Error: 'command' argument is required."
            return await self._execute_command("cli", cmd)
        if name == "web_fetch":
            return await handle_web_fetch(
                args, headless_binary=self._headless_binary
            )
        if name == "file_read":
            return handle_file_read(
                args, self._workspace_dir,
                persona_local_dir=self._persona_config["local_dir"],
            )
        if name == "file_write":
            return handle_file_write(args, self._workspace_dir)
        if name == "file_manage":
            return handle_file_manage(
                args, self._workspace_dir,
                persona_local_dir=self._persona_config["local_dir"],
            )
        if name == "skill_docs":
            return handle_skill_docs(args, self._skills_by_name)
        if name == "cron":
            return handle_cron_action(args, self._cron_scheduler)
        if name == "clarify":
            return handle_clarify(
                args,
                self._pending_clarify,
                chat_id=clarify_chat_id_var.get(),
            )
        return f"Error: unknown tool '{name}'."

    def pop_pending_clarify(self, chat_id: int) -> ClarifyRequest | None:
        """채널이 ``process_message`` 후 호출 — pending clarify 를 회수·제거한다.

        BIZ-260: 한 chat 의 다음 메시지가 도착하기 전까지 ``_pending_clarify[chat_id]``
        에 머무르지만, 채널이 인라인 키보드 렌더에 성공하면 즉시 제거해 다음 호출이
        깨끗한 상태에서 시작되도록 한다. 인라인 키보드를 지원하지 않는 채널
        (webhook 등) 은 이 메서드를 호출하지 않고 ``format_user_visible`` 텍스트를
        그대로 사용자에게 노출한다.
        """
        return self._pending_clarify.pop(chat_id, None)

    async def _dispatch_external_skill(self, args: dict) -> str:
        """execute_skill 도구 호출을 처리한다."""
        skill_name = args.get("skill_name", "")
        command = args.get("command", "")
        if command:
            return await self._execute_command(skill_name, command)
        skill_args = args.get("args", "")
        result = await self._execute_skill(skill_name, skill_args)
        return result or "[no output]"

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    # BIZ-252 — Anthropic prompt caching 경계.
    # 시스템 프롬프트를 (persona, skills, rag, react) 세그먼트로 쪼개되,
    # 각 세그먼트의 trailing separator 를 텍스트에 포함시켜 단순 합치기(``"".join``)가
    # 기존 ``"\n\n---\n\n".join(parts)`` 와 byte-identical 한 결과를 내도록 한다.
    # 이 덕분에 Claude 가 content blocks 리스트로 받아도, 비-Claude 프로바이더가
    # 평탄화 문자열로 받아도 동일한 prefix 가 노출된다.
    _SYSTEM_BLOCK_SEPARATOR = "\n\n---\n\n"

    def _build_system_blocks(self, rag_context: str = "") -> list[SystemBlock]:
        """페르소나·스킬·RAG·ReAct 지시문을 세그먼트(SystemBlock)로 반환한다.

        캐시 경계:
          - 1차: 페르소나 끝 (AGENT.md + USER.md + MEMORY.md)
          - 2차: 스킬 목록 끝
        ReAct 지시문과 RAG 블록은 캐시 마커 뒤에 둔다 (RAG 는 요청마다 변하므로
        무효화 회피, ReAct 는 작아 별도 마커가 불필요).

        Args:
            rag_context: ``_retrieve_relevant_context()`` 결과. 빈 문자열이면 블록을 생략한다.
        """
        # (text, cache) 쌍을 모은 뒤 마지막 블록을 제외한 모든 블록 끝에 separator 를 부착한다.
        segments: list[tuple[str, bool]] = []
        if self._persona_prompt:
            segments.append((self._persona_prompt, True))
        if self._skills_prompt:
            segments.append((self._skills_prompt, True))
        if rag_context:
            segments.append((rag_context, False))
        segments.append((_TOOL_USAGE_INSTRUCTION, False))

        blocks: list[SystemBlock] = []
        last = len(segments) - 1
        for idx, (text, cache) in enumerate(segments):
            suffix = self._SYSTEM_BLOCK_SEPARATOR if idx < last else ""
            blocks.append(SystemBlock(text=text + suffix, cache=cache))
        return blocks

    @staticmethod
    def _flatten_system_blocks(blocks: list[SystemBlock]) -> str:
        """``_build_system_blocks`` 결과를 단일 문자열로 합친다.

        각 블록 텍스트가 자체 separator 를 포함하므로 빈 문자열로 합쳐도
        기존 ``_build_system_prompt`` 와 byte-identical 한 결과를 낸다.
        """
        return "".join(b.text for b in blocks)

    def _build_system_prompt(self, rag_context: str = "") -> str:
        """레거시 단일-문자열 system prompt API.

        ``_build_system_blocks`` + ``_flatten_system_blocks`` 를 합친 얇은 래퍼.
        BIZ-252 이전 호출자(tests, docs) 호환용. 신규 호출 경로는
        ``_build_system_blocks`` 를 사용해 prompt caching 경계를 보존해야 한다.
        """
        return self._flatten_system_blocks(self._build_system_blocks(rag_context=rag_context))

    async def _retrieve_relevant_context(
        self,
        user_text: str,
        exclude_contents: set[str] | None = None,
    ) -> str:
        """사용자 질의와 의미상 가까운 과거 메시지를 회수하여 시스템 프롬프트 블록으로 포맷한다.

        설계 결정:
        - RAG가 비활성이거나 임베딩 서비스/모델 로드 실패 시 빈 문자열을 반환하여
          기존 슬라이딩 윈도우 동작으로 자연 fallback 한다(서비스 가용성 보존).
        - 최근 윈도우(``_history_limit``)에 이미 포함된 메시지는 ``exclude_contents``로 제외하여
          중복 주입을 방지한다.
        - 임계값(``_rag_threshold``) 이상 유사도만 채택 — 노이즈 회상으로 인한 오답을 줄인다.
        - 인코딩은 동기 API이므로 ``asyncio.to_thread``로 워커 스레드에 위임한다.
        - StructuredLogger가 주입되어 있으면 호출 단위로 ``rag_retrieve`` 액션을 적재해
          BIZ-29 토큰 절감 추세 분석(``simpleclaw.memory.stats.analyze_rag_logs``)에 사용한다.

        Args:
            user_text: 현재 사용자 메시지 원본.
            exclude_contents: 이미 ``messages`` 리스트에 들어간 메시지 본문 집합.

        Returns:
            "## 관련 과거 대화\\n..." 마크다운 블록. 회수 결과가 없으면 빈 문자열.
        """
        import asyncio
        import time

        # RAG 로그는 호출 단위로 1회만 기록한다(상태가 어떻든 추세 분석에 일관된 베이스 제공)
        start = time.perf_counter()

        def _log(
            *,
            status: str,
            hit: bool,
            candidates: int = 0,
            recalled_messages: int = 0,
            recalled_tokens: int = 0,
            top_score: float | None = None,
            error: str | None = None,
        ) -> None:
            if self._structured_logger is None:
                return
            details: dict = {
                "hit": hit,
                "candidates": candidates,
                "recalled_messages": recalled_messages,
                "recalled_tokens": recalled_tokens,
                "top_k": self._rag_top_k,
                "threshold": self._rag_threshold,
            }
            if top_score is not None:
                details["top_score"] = round(float(top_score), 4)
            if error is not None:
                details["error"] = error
            try:
                self._structured_logger.log(
                    action_type="rag_retrieve",
                    input_summary=user_text,
                    output_summary=f"recalled={recalled_messages} tokens={recalled_tokens}",
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                    status=status,
                    **details,
                )
            except Exception as exc:  # noqa: BLE001 — 로깅 실패가 회상을 막아선 안 됨
                logger.warning("RAG structured log write failed: %s", exc)

        if self._embedding_service is None or not self._embedding_service.is_enabled:
            _log(status="skipped", hit=False, error="rag_disabled")
            return ""

        try:
            query_vec = await asyncio.to_thread(
                self._embedding_service.encode_query, user_text
            )
        except Exception as exc:
            logger.warning("RAG query encoding failed: %s", exc)
            _log(status="error", hit=False, error=f"encode:{exc}"[:200])
            return ""
        if query_vec is None:
            _log(status="skipped", hit=False, error="encode_returned_none")
            return ""

        try:
            results = await asyncio.to_thread(
                self._store.search_similar,
                query_vec,
                self._rag_top_k,
            )
        except Exception as exc:
            logger.warning("RAG search failed: %s", exc)
            _log(status="error", hit=False, error=f"search:{exc}"[:200])
            return ""

        if not results:
            _log(status="success", hit=False, candidates=0)
            return ""

        excluded = exclude_contents or set()
        lines: list[str] = []
        recalled_tokens = 0
        recalled_messages = 0
        for msg, score in results:
            if score < self._rag_threshold:
                continue
            if msg.content in excluded:
                continue
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"- [{ts}] **{msg.role.value}**: {msg.content}"
            )
            recalled_messages += 1
            recalled_tokens += int(msg.token_count or 0)

        top_score = results[0][1] if results else None

        if not lines:
            _log(
                status="success",
                hit=False,
                candidates=len(results),
                top_score=top_score,
            )
            return ""

        _log(
            status="success",
            hit=True,
            candidates=len(results),
            recalled_messages=recalled_messages,
            recalled_tokens=recalled_tokens,
            top_score=top_score,
        )

        header = (
            "## 관련 과거 대화 (시맨틱 회상)\n\n"
            "아래는 현재 질문과 의미상 유사한 과거 대화입니다. "
            "최근 메시지 윈도우 밖의 정보일 수 있으니 응답 근거로 활용하세요."
        )
        return f"{header}\n\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    def _resolve_skill_name(self, name: str) -> SkillDefinition | None:
        """LLM이 반환한 스킬 이름을 등록된 스킬과 fuzzy-match한다."""
        if name in self._skills_by_name:
            return self._skills_by_name[name]

        lower = name.lower()
        for key, skill in self._skills_by_name.items():
            if key.lower() == lower:
                return skill

        normalized = lower.replace(" ", "-")
        for key, skill in self._skills_by_name.items():
            if key.lower() == normalized:
                return skill

        for key, skill in self._skills_by_name.items():
            if lower.replace("-", "").replace(" ", "") in key.lower().replace("-", ""):
                return skill

        return None

    def _resolve_command_timeout(self, command: str) -> int:
        """명령 문자열에 따라 실제로 적용할 타임아웃(초) 을 결정한다.

        BIZ-187: ``agent-browser`` 가 들어간 명령(특히 ``open && wait && text``
        composite chain)은 SPA(wikidocs.net 등)에서 60s 기본값을 안정적으로 넘긴다.
        ``agent_browser_command_timeout`` (기본 180s) 를 화이트리스트로 적용해 모델
        tool loop 가 ``Skill command timed out`` 으로 ``max_tool_iterations`` 를
        통째로 소진하는 패턴을 차단한다. 다른 명령은 기존 ``_skill_timeout`` 유지.
        """
        # ``agent-browser`` 가 들어가 있으면 합성 명령(``&&`` 로 묶인 chain)이든
        # 단일 명령이든 동일하게 확장 타임아웃을 적용한다. 단어 경계로 잡기 위해
        # ``"agent-browser "`` (뒤에 공백/플래그가 따라옴) 만 매치 — 우연한
        # 부분 문자열 일치 방지.
        if "agent-browser " in command or command.endswith("agent-browser"):
            if self._agent_browser_timeout > self._skill_timeout:
                return self._agent_browser_timeout
        return self._skill_timeout

    @staticmethod
    def _call_invokes_agent_browser(tool_call: ToolCall) -> bool:
        """tool_call 이 ``agent-browser`` CLI 를 실행하는지 판별한다.

        BIZ-190: per-turn ``agent-browser`` 호출 cap 카운터에서 사용. 라우팅
        경로가 ``execute_skill``(``skill_name=agent-browser`` 또는 ``command``
        문자열에 agent-browser 포함) 인 경우, ``cli``(``command`` 가 직접
        agent-browser 호출) 인 경우 모두 동일하게 카운트한다.
        """
        name = tool_call.name
        args = tool_call.arguments or {}
        if name == "execute_skill":
            if args.get("skill_name") == "agent-browser":
                return True
            cmd = str(args.get("command") or "")
            if AgentOrchestrator._is_agent_browser_command(cmd):
                return True
            inner_args = str(args.get("args") or "")
            # ``args`` 만 단독으로 agent-browser 호출을 담는 케이스(``args=
            # "open https://..."``) 도 있으나, 그건 skill_name 으로 이미 카운트됨.
            # 그 외 ``args`` 에 명시적으로 "agent-browser " 가 들어간 합성 형태도
            # 포함시킨다.
            if AgentOrchestrator._is_agent_browser_command(inner_args):
                return True
            return False
        if name == "cli":
            cmd = str(args.get("command") or "")
            return AgentOrchestrator._is_agent_browser_command(cmd)
        return False

    @staticmethod
    def _is_agent_browser_command(command: str) -> bool:
        """``command`` 가 ``agent-browser`` CLI 를 호출하는지 판별한다.

        BIZ-190: composite chain 차단·반복 호출 카운터에서 공유하는 단순 판별기.
        ``agent-browser`` 가 단어 경계(공백 뒤 / 명령 끝)로 나타나야 하므로
        우연한 부분 문자열 일치(``my-agent-browser-script`` 등) 를 배제한다.
        """
        return "agent-browser " in command or command.endswith("agent-browser")

    @staticmethod
    def _is_composite_agent_browser_chain(command: str) -> bool:
        """``agent-browser`` 가 ``&&``/``||``/``;`` 로 묶인 composite chain 인지 판별.

        BIZ-190: ``open && wait && evaluate`` 같은 한 줄 chain 은 BIZ-187 의 180s
        화이트리스트로도 안정적으로 끝나지 않고(중간 단계 daemon busy 등) tool
        loop 를 통째로 소모한다. 각 단계는 독립 tool call 로 쪼개야 함.

        파이프(``|``) 는 ``agent-browser get text | grep`` 같이 합리적인 후처리
        파이프라인으로 쓰일 가능성이 있어 차단하지 않는다.
        """
        if not AgentOrchestrator._is_agent_browser_command(command):
            return False
        # ``||`` 가 먼저 매칭되도록 substring 순서 주의 — 그리고 ``&&`` 는 ``&`` 보다
        # 우선. ``;`` 는 단독 매칭. 셸 인용("..."/'...') 안의 ``&&`` 까지는 보지 않음:
        # 인용 안에 ``&&`` 를 넣어 chain 흉내내는 패턴은 봇 운영 중 관찰된 적 없음.
        return ("&&" in command) or ("||" in command) or (";" in command)

    async def _execute_command(
        self, skill_name: str, command: str
    ) -> str:
        """셸 명령을 실행하고 출력을 반환한다.

        보안 절차:
        1. CommandGuard로 위험 명령 차단
        2. python 경로를 venv 내 python으로 자동 치환
        3. 환경변수 필터링 (env_passthrough만 전달)
        4. 프로세스 그룹 격리 (preexec_fn)
        5. 타임아웃 초과 시 프로세스 그룹 강제 종료
        """
        import asyncio

        try:
            self._command_guard.check(command)
        except DangerousCommandError as exc:
            logger.warning("Command blocked for skill '%s': %s", skill_name, exc)
            return f"Command blocked (dangerous pattern detected): {exc.description}"

        # BIZ-190: ``agent-browser`` composite chain 은 BIZ-187 의 180s 화이트리스트
        # 타임아웃 + 시스템 프롬프트 가드에도 불구하고 작은 모델이 첫 시도부터 다시
        # 보내는 패턴이 잔존. subprocess 진입 전에 차단하고 명확한 단일-호출 안내를
        # tool result 로 돌려준다 — 같은 turn 안에서 LLM 이 정정할 수 있도록.
        if self._is_composite_agent_browser_chain(command):
            logger.warning(
                "BIZ-190: composite agent-browser chain blocked for skill '%s': %s",
                skill_name, command[:200],
            )
            return _AGENT_BROWSER_COMPOSITE_BLOCKED_MESSAGE

        command = self._normalize_skill_command(command)
        # BIZ-187: 정규화 이후의 최종 command 문자열로 타임아웃을 결정한다.
        # _normalize_skill_command 가 ``agent-browser`` chain 은 건드리지 않으므로
        # 안전하지만, 향후 normalize 가 명령을 재작성해도 일관되게 동작하도록.
        effective_timeout = self._resolve_command_timeout(command)

        logger.info(
            "Executing skill command (timeout=%ds): %s",
            effective_timeout, command,
        )
        try:
            # workspace 디렉토리가 삭제되었을 수 있으므로 실행 직전에 보장
            self._workspace_dir.mkdir(parents=True, exist_ok=True)

            env = filter_env(passthrough=self._env_passthrough)
            env["AGENT_WORKSPACE"] = str(self._workspace_dir.resolve())

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workspace_dir),
                env=env,
                preexec_fn=get_preexec_fn(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout
            )
            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.error(
                    "Skill command failed (exit %d): %s",
                    proc.returncode, error,
                )
                # subprocess 의 stderr 는 외부 도구/원격 응답을 그대로 전달할
                # 수 있어 prompt-injection 가장 큰 surface. envelope + 길이
                # 캡 + framing 제거를 적용. (PRD §3.5.6)
                return sanitize_tool_error(
                    f"Command failed (exit {proc.returncode}): {error[:500]}"
                )

            logger.info(
                "Skill command succeeded: %d chars output", len(output)
            )
            return output if output else "[Command completed with no output]"

        except asyncio.TimeoutError:
            logger.error("Skill command timed out: %s", command)
            await kill_process_group(proc, metrics=self._metrics)
            return f"Command timed out after {effective_timeout}s"
        except Exception as exc:
            logger.error("Skill command error: %s", exc)
            return sanitize_tool_error(f"Command error: {str(exc)[:200]}")

    async def _execute_skill(
        self, skill_name: str, args_str: str
    ) -> str | None:
        """이름으로 스킬을 찾아 실행하고 출력을 반환한다."""
        skill = self._resolve_skill_name(skill_name)
        if skill is None:
            logger.warning("Skill '%s' not found in registry", skill_name)
            return f"[Skill '{skill_name}' not found. Available: {', '.join(self._skills_by_name.keys())}]"

        if not skill.script_path:
            skill_md = Path(skill.skill_dir) / "SKILL.md"
            if skill_md.is_file():
                content = skill_md.read_text(encoding="utf-8")[:2000]
                return f"[Skill documentation for {skill_name}]:\n{content}"
            return None

        try:
            args = args_str.split() if args_str else None
            result = await execute_skill(
                skill,
                args=args,
                timeout=self._skill_timeout,
                metrics=self._metrics,
            )
            logger.info(
                "Skill '%s' executed: success=%s", skill_name, result.success
            )
            return result.output
        except Exception as exc:
            logger.error("Skill '%s' execution failed: %s", skill_name, exc)
            return f"Error executing skill {skill_name}: {str(exc)[:200]}"

    # ------------------------------------------------------------------
    # Skill formatting
    # ------------------------------------------------------------------

    def _format_skills_for_prompt(self, skills: list[SkillDefinition]) -> str:
        """시스템 프롬프트용 스킬 개요 목록을 생성한다.

        BIZ-166: 각 skill 옆에 정확한 호출 형식을 명시한다. 모델이 `uvx <name>` /
        `<name> "..."` 같은 추측으로 첫 시도를 낭비하지 않도록.
        """
        if not skills:
            return ""
        lines = [
            "## Available Skills",
            "",
            (
                "Invoke each skill via `execute_skill` with `skill_name` + `args`. "
                "Do NOT compose your own bare command — the runtime resolves the "
                "venv path for you. NEVER prefix the skill name with `uvx` or "
                "`pipx run`; these skills are NOT on PyPI."
            ),
            "",
        ]
        for skill in skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
            # script_path 가 .py 인 skill 은 venv 가 자동 해결되므로 args 만 전달하면 됨.
            # 그렇지 않은 skill (예: agent-browser 같은 CLI 묶음) 은 SKILL.md 참조 안내.
            script_path = Path(skill.script_path) if skill.script_path else None
            if (
                script_path is not None
                and script_path.suffix == ".py"
                and script_path.is_file()
            ):
                lines.append(
                    f"  Invocation: `execute_skill(skill_name=\"{skill.name}\", "
                    f"args=\"<positional args>\")`"
                )
            else:
                lines.append(
                    f"  Invocation: call `skill_docs(\"{skill.name}\")` first to "
                    f"read the exact command sequence."
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_skill_command(self, command: str) -> str:
        """셸 명령을 실행 가능한 형태로 정규화한다.

        BIZ-166: 모델이 ``execute_skill({"command": "news-search-skill ..."})`` 처럼
        bare skill 이름으로 명령을 보내면 PATH 에 그런 실행 파일이 없어 실패한다.
        이를 ``<venv>/bin/python <script_path> <rest>`` 로 자동 치환해 첫 시도가
        성공하도록 한다. agent-browser 같이 ``&&`` 로 묶인 composite 명령은
        건드리지 않는다 (등록된 skill 이름이 아니면 통과).

        BIZ-166 follow-up: ``uvx <skill-name> ...`` / ``pipx run <skill-name> ...``
        같이 등록된 skill 을 패키지 레지스트리에서 가져오려는 패턴도 동일하게
        venv-direct 로 치환한다 (gemini-3-flash-preview 가 시스템 프롬프트의
        금지 안내를 무시하고 이 형태로 첫 시도하는 사고 다발 — 2026-05-12).

        기존 ``_fix_python_path`` 동작도 흡수 — ``python/python3 script.py`` 의
        인터프리터 부분을 스크립트 인근 venv 의 python 으로 치환한다.
        """
        import shlex

        parts = command.split(None, 1)
        if not parts:
            return command

        first_token, rest = parts[0], parts[1] if len(parts) > 1 else ""

        # BIZ-166: 첫 토큰이 등록된 skill 이름이고 python 스크립트면 venv-direct 호출로 치환.
        # ``&&`` / ``|`` 같은 shell 연산자가 포함된 composite 명령은 등록 skill 이름과
        # 일치할 수 없으므로 자연 통과 (예: ``agent-browser open ... && agent-browser wait ...``).
        skill = getattr(self, "_skills_by_name", {}).get(first_token)
        if skill is not None and skill.script_path:
            script_path = Path(skill.script_path)
            if script_path.suffix == ".py" and script_path.is_file():
                venv_python = self._find_venv_python(script_path)
                if venv_python is not None:
                    rewritten = (
                        f"{venv_python} {script_path} {rest}".rstrip()
                    )
                    logger.info(
                        "BIZ-166: rewrote bare skill invocation '%s' → '%s %s ...'",
                        first_token, venv_python.name, script_path.name,
                    )
                    return rewritten

        # BIZ-166 follow-up: ``uvx <skill-name> ...`` / ``pipx run <skill-name> ...``
        # 형태도 등록된 .py skill 이면 venv-direct 로 치환. 첫 토큰이 prefix runner
        # 일 때만 동작하므로 다른 ``uvx`` 사용 사례(예: 진짜 PyPI 패키지)는 통과.
        prefix_runner = None
        prefix_skip = 0
        if first_token == "uvx":
            prefix_runner = "uvx"
            prefix_skip = 1
        elif first_token == "pipx" and rest.split(None, 1)[:1] == ["run"]:
            prefix_runner = "pipx run"
            prefix_skip = 2

        if prefix_runner is not None:
            inner_tokens = command.split(None, prefix_skip + 1)
            if len(inner_tokens) >= prefix_skip + 1:
                inner_first = inner_tokens[prefix_skip]
                inner_rest = (
                    inner_tokens[prefix_skip + 1]
                    if len(inner_tokens) > prefix_skip + 1
                    else ""
                )
                inner_skill = getattr(self, "_skills_by_name", {}).get(
                    inner_first
                )
                if inner_skill is not None and inner_skill.script_path:
                    inner_script = Path(inner_skill.script_path)
                    if (
                        inner_script.suffix == ".py"
                        and inner_script.is_file()
                    ):
                        venv_python = self._find_venv_python(inner_script)
                        if venv_python is not None:
                            rewritten = (
                                f"{venv_python} {inner_script} {inner_rest}"
                            ).rstrip()
                            logger.info(
                                "BIZ-166: rewrote '%s %s ...' → '%s %s ...'",
                                prefix_runner, inner_first,
                                venv_python.name, inner_script.name,
                            )
                            return rewritten

        # 기존 동작: python/python3 인터프리터를 venv 의 python 으로 치환.
        if first_token not in ("python", "python3"):
            return command

        try:
            tokens = shlex.split(rest)
        except ValueError:
            tokens = rest.split()

        script_path = None
        for token in tokens:
            if token.endswith(".py") and Path(token).is_file():
                script_path = Path(token)
                break

        if script_path is None:
            if first_token == "python":
                return f"python3 {rest}"
            return command

        venv_python = self._find_venv_python(script_path)
        if venv_python is not None:
            return f"{venv_python} {rest}"

        if first_token == "python":
            return f"python3 {rest}"
        return command

    @staticmethod
    def _find_venv_python(script_path: Path) -> Path | None:
        """스크립트 인근 venv 의 python 실행 파일 경로를 찾는다.

        검색 순서: ``<script_parent>/venv``, ``<script_parent.parent>/venv``,
        그리고 ``.venv`` 변종. 없으면 None.
        """
        for venv_dir in (
            script_path.parent / "venv",
            script_path.parent.parent / "venv",
            script_path.parent / ".venv",
            script_path.parent.parent / ".venv",
        ):
            venv_python = venv_dir / "bin" / "python"
            if venv_python.is_file():
                return venv_python
        return None

    def _load_skills_config(self) -> dict:
        """config.yaml에서 skills 섹션을 로드한다."""
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}

    def _load_security_config(self) -> dict:
        """config.yaml에서 security 섹션을 로드한다."""
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            sec = data.get("security", {}) if isinstance(data, dict) else {}
            return sec if isinstance(sec, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}
