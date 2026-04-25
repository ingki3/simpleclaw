"""Agent orchestrator — 페르소나·스킬·메모리·LLM을 하나로 묶는 중앙 조율기.

응답 파이프라인 (OpenClaw-inspired):
1. 사용자 메시지 수신
2. LLM에 스킬 라우팅 질의 ("이 메시지에 스킬이 필요한가?")
3. 스킬이 필요하면 → 실행 → 결과를 컨텍스트에 포함
4. 전체 컨텍스트(페르소나 + 대화 이력 + 스킬 결과)로 최종 응답 생성

Hot-reload 정책:
  AGENT.md, USER.md, MEMORY.md, 스킬/레시피 파일은 매 메시지(process_message /
  process_cron_message) 진입 시 1회 디스크에서 다시 읽는다.
  → 파일 수정 후 봇 리스타트 없이 다음 메시지부터 반영됨.
  → ReAct 루프 내부에서는 캐시된 값을 재사용하여 불필요한 I/O를 방지함.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from simpleclaw.config import load_agent_config, load_persona_config
from simpleclaw.llm.models import LLMRequest
from simpleclaw.llm.router import create_router
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.recipes.loader import discover_recipes
from simpleclaw.security import (
    CommandGuard,
    DangerousCommandError,
    filter_env,
    get_preexec_fn,
    kill_process_group,
)
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.models import SkillDefinition

from simpleclaw.agent.builtin_tools import (
    BUILTIN_TOOL_NAMES,
    handle_cron_action,
    handle_file_manage,
    handle_file_read,
    handle_file_write,
    handle_skill_docs,
    handle_web_fetch,
)
from simpleclaw.agent.commands import try_cron_command, try_recipe_command
from simpleclaw.agent.prompts import (
    CLI_TOOL_PROMPT,
    CRON_TOOL_PROMPT,
    FILE_MANAGE_TOOL_PROMPT,
    FILE_READ_TOOL_PROMPT,
    FILE_WRITE_TOOL_PROMPT,
    REACT_SYSTEM_PROMPT,
    REACT_USER_PROMPT,
    SKILL_DOCS_TOOL_PROMPT,
    WEB_FETCH_TOOL_PROMPT,
)
from simpleclaw.agent.react import parse_react

if TYPE_CHECKING:
    from simpleclaw.daemon.scheduler import CronScheduler

logger = logging.getLogger(__name__)

# Prompt budget tiers (character counts for the skills section)
_PROMPT_BUDGET_TIER1_LIMIT = 18_000
_PROMPT_BUDGET_TIER2_LIMIT = 24_000


class AgentOrchestrator:
    """페르소나 + 스킬 + 대화 이력 + LLM을 조합하는 중앙 오케스트레이터.

    응답 파이프라인:
    1. 시스템 프롬프트 조립 (페르소나 + 스킬 목록)
    2. 스킬 라우팅: LLM에게 스킬 필요 여부 질의
    3. 필요 시: 스킬 실행 → 결과를 컨텍스트에 추가
    4. 전체 컨텍스트로 최종 응답 생성
    5. 대화 저장
    """

    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        self._config_path = Path(config_path)

        # --- 정적 설정 로드 (리스타트 시에만 갱신) ---
        agent_config = load_agent_config(config_path)
        persona_config = load_persona_config(config_path)

        self._history_limit = agent_config["history_limit"]

        # 페르소나·스킬 설정값 보관 — _reload_dynamic_files()에서 참조
        self._persona_config = persona_config
        skills_config = self._load_skills_config()
        self._skills_config = skills_config

        # 초기 로드: 페르소나·스킬 파일을 디스크에서 읽어 캐시 필드 채움
        # (이후 매 메시지 진입 시 _reload_dynamic_files()로 갱신)
        self._reload_dynamic_files()

        # Cron scheduler — injected after construction via set_cron_scheduler()
        # Must be initialized before _format_skills_for_router (budget estimation)
        self._cron_scheduler: CronScheduler | None = None

        # LLM router
        self._router = create_router(config_path)

        # Conversation store
        db_path = Path(agent_config["db_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._store = ConversationStore(db_path)

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

        # Multi-turn tool execution
        self._max_tool_iterations = agent_config.get("max_tool_iterations", 5)

        # Workspace directory for skill file output
        self._workspace_dir = Path(
            agent_config.get("workspace_dir", ".agent/workspace")
        )
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "AgentOrchestrator initialized: persona=%d chars, skills=%d, backend=%s",
            len(self._persona_prompt),
            len(self._skills),
            self._router.get_default_backend(),
        )

    def _reload_dynamic_files(self) -> None:
        """페르소나·스킬 파일을 디스크에서 다시 읽어 캐시 필드를 갱신한다 (hot-reload).

        호출 시점: __init__() 초기화 + 매 메시지 진입 시 1회.
        ReAct 루프 내부에서는 호출하지 않아 불필요한 I/O를 방지한다.
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
        # 시스템 프롬프트용 스킬 목록 (사용자에게 보여지는 형태)
        self._skills_prompt = self._format_skills_for_prompt(self._skills)
        # 라우팅 판단용 스킬 목록 (LLM이 스킬 선택 시 참조)
        self._skills_router_list = self._format_skills_for_router(self._skills)
        # skill-docs 도구용 상세 목록 (SKILL.md 내용 포함)
        self._skills_router_list_with_usage = self._format_skills_for_router_with_usage(self._skills)

    def set_cron_scheduler(self, scheduler: CronScheduler) -> None:
        """Inject CronScheduler so the agent can manage cron jobs via /cron commands."""
        self._cron_scheduler = scheduler
        logger.info("CronScheduler injected into AgentOrchestrator.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_cron_message(self, text: str) -> str:
        """크론 잡 메시지를 격리된 컨텍스트로 처리한다.

        ReAct 루프를 사용하되, 대화 이력을 불러오지 않고
        공유 대화 DB에 메시지를 저장하지 않는다.
        """
        # 매 메시지 진입 시 1회 hot-reload (파일 변경 반영)
        self._reload_dynamic_files()
        return await self._react_loop(text, isolated=True)

    async def process_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str:
        """수신 메시지를 ReAct 파이프라인으로 처리한다."""
        # 매 메시지 진입 시 1회 hot-reload (파일 변경 반영)
        self._reload_dynamic_files()

        # /cron 명령어 확인
        cron_result = try_cron_command(text, self._cron_scheduler)
        if cron_result is not None:
            self._store.add_message(ConversationMessage(
                role=MessageRole.USER, content=text,
            ))
            self._store.add_message(ConversationMessage(
                role=MessageRole.ASSISTANT, content=cron_result,
            ))
            return cron_result

        # /recipe-name 명령어 확인 (e.g. /ai-report)
        recipe_result = await try_recipe_command(text, self._react_loop)
        if recipe_result is not None:
            self._store.add_message(ConversationMessage(
                role=MessageRole.USER, content=text,
            ))
            self._store.add_message(ConversationMessage(
                role=MessageRole.ASSISTANT, content=recipe_result,
            ))
            return recipe_result

        response_text = await self._react_loop(text)

        self._store.add_message(ConversationMessage(
            role=MessageRole.USER, content=text,
        ))
        self._store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content=response_text,
        ))

        return response_text

    # ------------------------------------------------------------------
    # Routing & dispatch
    # ------------------------------------------------------------------

    async def _dispatch_routing(
        self, routing: dict
    ) -> tuple[str | None, str | None]:
        """라우팅 결정 1건을 실행하여 (skill_name, result) 튜플을 반환한다.

        Args:
            routing: ReAct 파서가 추출한 Action JSON.
                     필수 키: skill_name / 선택 키: command, args
        """
        skill_name = routing.get("skill_name", "")
        command = routing.get("command", "")

        logger.info(
            "Skill router selected: %s (command: %s)",
            skill_name, command,
        )

        # 내장 도구 (cron, cli, web-fetch, file-* 등) — 외부 프로세스 없이 처리
        if skill_name in BUILTIN_TOOL_NAMES:
            result = await self._dispatch_builtin(skill_name, routing)
            return skill_name, result

        if command:
            result = await self._execute_command(skill_name, command)
        else:
            skill_args = routing.get("args", "")
            result = await self._execute_skill(skill_name, skill_args)

        return skill_name, result

    async def _dispatch_builtin(
        self, tool_name: str, routing: dict
    ) -> str:
        """내장 도구를 실행한다. tool_name으로 분기하여 각 핸들러에 위임."""
        if tool_name == "cron":
            return handle_cron_action(routing, self._cron_scheduler)
        if tool_name == "cli":
            cmd = routing.get("command", "")
            if not cmd:
                return "Error: 'command' field is required."
            return await self._execute_command("cli", cmd)
        if tool_name == "web-fetch":
            return await handle_web_fetch(routing)
        if tool_name == "file-read":
            return handle_file_read(routing, self._workspace_dir)
        if tool_name == "file-write":
            return handle_file_write(routing, self._workspace_dir)
        if tool_name == "file-manage":
            return handle_file_manage(routing, self._workspace_dir)
        if tool_name == "skill-docs":
            return handle_skill_docs(routing, self._skills_by_name)
        return f"Error: unknown built-in tool '{tool_name}'."

    # ------------------------------------------------------------------
    # ReAct loop
    # ------------------------------------------------------------------

    async def _react_loop(
        self, text: str, isolated: bool = False
    ) -> str:
        """ReAct (Reasoning + Acting) 루프를 실행한다.

        매 반복마다 LLM에게 Thought/Action/Answer 형식의 응답을 요청한다.
        - Answer가 반환되면 즉시 종료
        - Action이 반환되면 스킬 실행 후 Observation을 trace에 추가
        - 어느 형식도 아니면 raw 텍스트를 그대로 반환 (fallback)
        - max_tool_iterations 초과 시 _generate_final()로 강제 종결

        Args:
            text: 사용자 원본 메시지
            isolated: True면 대화 이력 없이 독립 실행 (크론 잡 등)
        """
        trace: list[str] = []

        for i in range(self._max_tool_iterations):
            raw = await self._react_step(text, trace, isolated)
            thought, action, answer = parse_react(raw)

            if thought:
                trace.append(f"Thought: {thought}")
                logger.info("ReAct [%d] Thought: %s", i + 1, thought[:100])

            # Answer가 파싱되면 최종 응답으로 반환
            if answer is not None:
                logger.info("ReAct [%d] Answer: %d chars", i + 1, len(answer))
                return answer

            if action:
                # Action → 스킬 실행 → Observation을 trace에 기록
                trace.append(
                    f"Action: {json.dumps(action, ensure_ascii=False)}"
                )
                skill_name, result = await self._dispatch_routing(action)
                observation = result or "[no output]"
                trace.append(f"Observation: {observation[:2000]}")
                logger.info(
                    "ReAct [%d] Action: %s → %d chars",
                    i + 1, skill_name, len(observation),
                )
            else:
                # Action/Answer 모두 없음 → 일반 텍스트 응답으로 간주
                logger.info("ReAct [%d] fallback: raw response", i + 1)
                return raw

        # 최대 반복 횟수 도달 — trace를 기반으로 최종 응답 생성
        logger.warning(
            "ReAct max iterations (%d) reached, generating final answer",
            self._max_tool_iterations,
        )
        return await self._generate_final(text, trace, isolated)

    async def _react_step(
        self,
        user_message: str,
        trace: list[str],
        isolated: bool = False,
    ) -> str:
        """ReAct 루프 1회분: 현재 trace와 함께 LLM을 호출하여 응답을 받는다.

        시스템 프롬프트 구성:
          [페르소나 + 스킬 목록] + [ReAct 지시문 + 내장 도구 명세]

        Args:
            user_message: 사용자 원본 메시지
            trace: 이전 Thought/Action/Observation 기록
            isolated: True면 대화 이력 없이 독립 실행
        """
        # 시스템 프롬프트 = 페르소나·스킬 + ReAct 지시문·내장 도구
        system_prompt = self._build_system_prompt()
        builtin_tools = self._build_builtin_tools_prompt()
        react_system = REACT_SYSTEM_PROMPT.format(
            skills_list=self._skills_router_list or "(no tools)",
            max_iterations=self._max_tool_iterations,
            builtin_tools=builtin_tools,
        )
        full_system = f"{system_prompt}\n\n---\n\n{react_system}"

        trace_text = "\n".join(trace) if trace else "(no previous steps)"

        # 현재 시각을 KST로 주입하여 LLM이 시간 맥락을 파악하게 함
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        datetime_context = now_kst.strftime(
            "[현재 시각: %Y-%m-%d %H:%M (%A) KST]"
        )

        user_prompt = REACT_USER_PROMPT.format(
            datetime_context=datetime_context,
            react_trace=trace_text,
            user_message=user_message,
        )

        # isolated=True(크론 잡): 이력 없이 단일 메시지만 전송
        # isolated=False(일반): 최근 대화 이력 + 현재 메시지 전송
        if isolated:
            messages = [{"role": "user", "content": user_prompt}]
        else:
            recent = self._store.get_recent(limit=self._history_limit)
            messages = [
                {"role": msg.role.value, "content": msg.content}
                for msg in recent
            ]
            messages.append({"role": "user", "content": user_prompt})

        try:
            request = LLMRequest(
                system_prompt=full_system,
                user_message=user_prompt,
                messages=messages,
            )
            response = await self._router.send(request)
            return response.text.strip()
        except Exception as exc:
            logger.error("ReAct LLM error: %s", exc)
            return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

    async def _generate_final(
        self,
        user_message: str,
        trace: list[str],
        isolated: bool = False,
    ) -> str:
        """최대 반복 도달 시 trace 기반으로 최종 응답을 생성한다."""
        system_prompt = self._build_system_prompt()
        trace_text = "\n".join(trace)
        final_prompt = (
            f"Based on the following reasoning and observations, "
            f"provide the best possible answer to the user.\n\n"
            f"## Trace\n{trace_text}\n\n"
            f"## User Request\n{user_message}\n\n"
            f"Respond in the same language as the user. "
            f"If information is incomplete, honestly state what is missing."
        )

        if isolated:
            messages = [{"role": "user", "content": final_prompt}]
        else:
            recent = self._store.get_recent(limit=self._history_limit)
            messages = [
                {"role": msg.role.value, "content": msg.content}
                for msg in recent
            ]
            messages.append({"role": "user", "content": final_prompt})

        try:
            request = LLMRequest(
                system_prompt=system_prompt,
                user_message=final_prompt,
                messages=messages,
            )
            response = await self._router.send(request)
            return response.text.strip()
        except Exception as exc:
            logger.error("ReAct final generation error: %s", exc)
            return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_builtin_tools_prompt(self) -> str:
        """ReAct 시스템 프롬프트에 포함할 내장 도구 명세를 조립한다."""
        sections = [
            SKILL_DOCS_TOOL_PROMPT,
            CLI_TOOL_PROMPT,
            WEB_FETCH_TOOL_PROMPT,
            FILE_READ_TOOL_PROMPT,
            FILE_WRITE_TOOL_PROMPT,
            FILE_MANAGE_TOOL_PROMPT,
        ]

        if self._cron_scheduler is not None:
            recipes = discover_recipes(".agent/recipes")
            if recipes:
                recipe_lines = []
                for r in recipes:
                    desc = r.description or "(no description)"
                    recipe_lines.append(
                        f"- **{r.name}**: {desc} → `{r.recipe_dir}/recipe.yaml`"
                    )
                available_recipes = "\n".join(recipe_lines)
            else:
                available_recipes = "(등록된 레시피 없음)"
            sections.append(
                CRON_TOOL_PROMPT.format(available_recipes=available_recipes)
            )

        return "\n\n".join(sections)

    def _build_system_prompt(self) -> str:
        """캐시된 페르소나·스킬 텍스트를 조합하여 시스템 프롬프트를 반환한다.

        NOTE: 디스크 리로드는 여기서 하지 않는다.
        process_message / process_cron_message 진입 시 1회
        _reload_dynamic_files()가 호출되므로, 여기서는 캐시된 값만 사용.
        """
        parts = []

        if self._persona_prompt:
            parts.append(self._persona_prompt)

        if self._skills_prompt:
            parts.append(self._skills_prompt)

        if not parts:
            return (
                "You are SimpleClaw, a helpful personal assistant agent. "
                "Respond in the same language the user writes in. "
                "Be concise and helpful."
            )

        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # Skill execution
    # ------------------------------------------------------------------

    def _resolve_skill_name(self, name: str) -> SkillDefinition | None:
        """LLM이 반환한 스킬 이름을 등록된 스킬과 fuzzy-match한다.

        매칭 우선순위:
        1. 정확히 일치
        2. 대소문자 무시 일치
        3. 공백→하이픈 정규화 후 일치
        4. 구분자 제거 후 부분 문자열 포함
        """
        # 1단계: 정확 매치
        if name in self._skills_by_name:
            return self._skills_by_name[name]

        # 2단계: 대소문자 무시
        lower = name.lower()
        for key, skill in self._skills_by_name.items():
            if key.lower() == lower:
                return skill

        # 3단계: 공백→하이픈 정규화 (e.g. "ai report" → "ai-report")
        normalized = lower.replace(" ", "-")
        for key, skill in self._skills_by_name.items():
            if key.lower() == normalized:
                return skill

        # 4단계: 구분자 제거 후 부분 문자열 매치
        for key, skill in self._skills_by_name.items():
            if lower.replace("-", "").replace(" ", "") in key.lower().replace("-", ""):
                return skill

        return None

    async def _execute_command(
        self, skill_name: str, command: str
    ) -> str:
        """스킬 라우터가 요청한 셸 명령을 실행하고 출력을 반환한다.

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

        command = self._fix_python_path(command)

        logger.info("Executing skill command: %s", command)
        try:
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
                proc.communicate(), timeout=self._skill_timeout
            )
            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.error(
                    "Skill command failed (exit %d): %s",
                    proc.returncode, error,
                )
                return f"Command failed (exit {proc.returncode}): {error[:500]}"

            logger.info(
                "Skill command succeeded: %d chars output", len(output)
            )
            return output if output else "[Command completed with no output]"

        except asyncio.TimeoutError:
            logger.error("Skill command timed out: %s", command)
            await kill_process_group(proc)
            return f"Command timed out after {self._skill_timeout}s"
        except Exception as exc:
            logger.error("Skill command error: %s", exc)
            return f"Command error: {str(exc)[:200]}"

    async def _execute_skill(
        self, skill_name: str, args_str: str
    ) -> str | None:
        """이름으로 스킬을 찾아 실행하고 출력을 반환한다.

        script_path가 없는 스킬은 SKILL.md 문서를 대신 반환한다.
        """
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
                skill, args=args, timeout=self._skill_timeout
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
        """사용자에게 보여지는 시스템 프롬프트용 스킬 목록을 생성한다."""
        if not skills:
            return ""
        lines = ["## Available Skills", ""]
        for skill in skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    @staticmethod
    def _truncate_description(desc: str, max_len: int = 120) -> str:
        """Truncate a description to the first sentence or max_len chars."""
        end = desc.find(". ")
        if end > 0:
            desc = desc[:end + 1]
        if len(desc) > max_len:
            desc = desc[:max_len - 3] + "..."
        return desc

    def _format_skills_for_router(self, skills: list[SkillDefinition]) -> str:
        """프롬프트 버짓에 맞춰 스킬 목록을 단계적으로 축약한다.

        Tier 1: 이름 + 요약 설명 (18K자 이내)
        Tier 2: 이름만 (24K자 이내)
        Tier 3: 이분 탐색으로 들어갈 수 있는 최대 스킬 수만 포함
        """
        if not skills:
            return ""

        # Tier 1: 이름 + 요약 설명
        tier1 = self._format_skills_tier1(skills)
        if self._estimate_prompt_size(tier1) <= _PROMPT_BUDGET_TIER1_LIMIT:
            return tier1

        # Tier 2: 이름만 (설명 제거)
        tier2 = self._format_skills_tier2(skills)
        if self._estimate_prompt_size(tier2) <= _PROMPT_BUDGET_TIER2_LIMIT:
            logger.info(
                "Prompt budget: Tier 2 (names only) for %d skills", len(skills)
            )
            return tier2

        # Tier 3: 이분 탐색으로 최대 포함 가능 스킬 수 결정
        logger.warning(
            "Prompt budget: Tier 3 (truncated) for %d skills", len(skills)
        )
        return self._format_skills_tier3(skills)

    def _format_skills_tier1(self, skills: list[SkillDefinition]) -> str:
        """Tier 1: name + truncated description."""
        lines = []
        for skill in skills:
            desc = self._truncate_description(skill.description or "")
            lines.append(f"- {skill.name}: {desc}")
        return "\n".join(lines)

    def _format_skills_tier2(self, skills: list[SkillDefinition]) -> str:
        """Tier 2: names only."""
        return "\n".join(f"- {skill.name}" for skill in skills)

    def _format_skills_tier3(self, skills: list[SkillDefinition]) -> str:
        """Tier 3: binary search for max number of skills that fit."""
        lo, hi = 0, len(skills)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            text = self._format_skills_tier2(skills[:mid])
            if self._estimate_prompt_size(text) <= _PROMPT_BUDGET_TIER2_LIMIT:
                lo = mid
            else:
                hi = mid - 1

        text = self._format_skills_tier2(skills[:lo])
        if lo < len(skills):
            text += f"\n... and {len(skills) - lo} more skills (use skill-docs to discover)"
        return text

    def _estimate_prompt_size(self, skills_text: str) -> int:
        """스킬 섹션을 포함한 전체 시스템 프롬프트 크기를 추정한다 (문자 수 기준)."""
        base = len(self._persona_prompt) + 2000  # ReAct 템플릿 오버헤드
        builtin = len(self._build_builtin_tools_prompt())
        return base + builtin + len(skills_text)

    def _format_skills_for_router_with_usage(
        self, skills: list[SkillDefinition]
    ) -> str:
        """skill-docs 도구용 상세 스킬 목록을 생성한다 (SKILL.md 내용 포함)."""
        if not skills:
            return ""
        entries = []
        for skill in skills:
            entry = f"### {skill.name}\n{skill.description}\n"
            skill_md = Path(skill.skill_dir) / "SKILL.md"
            if skill_md.is_file():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    if len(content) > 800:
                        content = content[:800] + "..."
                    entry += f"\n{content}\n"
                except OSError:
                    pass
            entries.append(entry)
        return "\n---\n".join(entries)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_python_path(command: str) -> str:
        """python/python3 호출을 스크립트 인근 venv의 python으로 치환한다.

        탐색 순서: script.py 기준 ./venv, ../venv, ./.venv, ../.venv
        """
        import shlex

        parts = command.split(None, 1)
        if len(parts) < 2:
            return command

        interpreter, rest = parts[0], parts[1]

        if interpreter not in ("python", "python3"):
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
            if interpreter == "python":
                return f"python3 {rest}"
            return command

        for venv_dir in (
            script_path.parent / "venv",
            script_path.parent.parent / "venv",
            script_path.parent / ".venv",
            script_path.parent.parent / ".venv",
        ):
            venv_python = venv_dir / "bin" / "python"
            if venv_python.is_file():
                return f"{venv_python} {rest}"

        if interpreter == "python":
            return f"python3 {rest}"
        return command

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
