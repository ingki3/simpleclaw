"""Agent orchestrator: ties persona, skills, memory, and LLM together.

Response process (OpenClaw-inspired):
1. Receive user message
2. Ask LLM: "Does this message need a skill?" (skill routing)
3. If skill needed → execute skill → include result in context
4. Generate final response with full context (persona + history + skill result)
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
    """Central orchestrator that assembles persona + skills + history + LLM.

    Response pipeline:
    1. Build system prompt (persona + skill list)
    2. Skill routing: ask LLM if a skill is needed
    3. If needed: execute skill, add result to context
    4. Generate final response with full context
    5. Store conversation
    """

    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        self._config_path = Path(config_path)

        # Load configs
        agent_config = load_agent_config(config_path)
        persona_config = load_persona_config(config_path)

        self._history_limit = agent_config["history_limit"]

        # Persona — resolve and assemble once (cached)
        persona_files = resolve_persona_files(
            local_dir=persona_config["local_dir"],
            global_dir=persona_config["global_dir"],
        )
        assembly = assemble_prompt(
            persona_files, persona_config["token_budget"]
        )
        self._persona_prompt = assembly.assembled_text or ""

        # Cron scheduler — injected after construction via set_cron_scheduler()
        # Must be initialized before _format_skills_for_router (budget estimation)
        self._cron_scheduler: CronScheduler | None = None

        # Skills — discover once, store as dict for lookup
        skills_config = self._load_skills_config()
        self._skills = discover_skills(
            local_dir=skills_config.get("local_dir", ".agent/skills"),
            global_dir=skills_config.get("global_dir", "~/.agents/skills"),
        )
        self._skills_by_name: dict[str, SkillDefinition] = {
            s.name: s for s in self._skills
        }
        self._skills_prompt = self._format_skills_for_prompt(self._skills)
        self._skills_router_list = self._format_skills_for_router(self._skills)
        self._skills_router_list_with_usage = self._format_skills_for_router_with_usage(self._skills)

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

    def set_cron_scheduler(self, scheduler: CronScheduler) -> None:
        """Inject CronScheduler so the agent can manage cron jobs via /cron commands."""
        self._cron_scheduler = scheduler
        logger.info("CronScheduler injected into AgentOrchestrator.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_cron_message(self, text: str) -> str:
        """Process a cron job message with isolated context.

        Uses the ReAct loop but does NOT load conversation history
        and does NOT store messages in the shared conversation DB.
        """
        return await self._react_loop(text, isolated=True)

    async def process_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str:
        """Process an incoming message through the ReAct pipeline."""
        # Check for /cron commands
        cron_result = try_cron_command(text, self._cron_scheduler)
        if cron_result is not None:
            self._store.add_message(ConversationMessage(
                role=MessageRole.USER, content=text,
            ))
            self._store.add_message(ConversationMessage(
                role=MessageRole.ASSISTANT, content=cron_result,
            ))
            return cron_result

        # Check for /recipe-name commands
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
        """Execute a single routing decision and return (skill_name, result)."""
        skill_name = routing.get("skill_name", "")
        command = routing.get("command", "")

        logger.info(
            "Skill router selected: %s (command: %s)",
            skill_name, command,
        )

        # Built-in tools — handled internally
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
        """Dispatch a built-in tool action."""
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
        """Execute the ReAct (Reasoning + Acting) loop."""
        trace: list[str] = []

        for i in range(self._max_tool_iterations):
            raw = await self._react_step(text, trace, isolated)
            thought, action, answer = parse_react(raw)

            if thought:
                trace.append(f"Thought: {thought}")
                logger.info("ReAct [%d] Thought: %s", i + 1, thought[:100])

            if answer is not None:
                logger.info("ReAct [%d] Answer: %d chars", i + 1, len(answer))
                return answer

            if action:
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
                # First iteration without Action/Answer → LLM ignored ReAct format.
                # Retry once by injecting the raw response as a failed thought.
                if i == 0:
                    logger.warning(
                        "ReAct [%d] no format detected, retrying with nudge", i + 1
                    )
                    trace.append(
                        f"Thought: {raw[:500]}\n"
                        "Observation: [SYSTEM] You must use the Thought/Action/Answer format. "
                        "Use a tool to get real-time information. Do NOT answer from memory."
                    )
                    continue
                logger.info("ReAct [%d] fallback: raw response", i + 1)
                return raw

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
        """Execute one ReAct step: send trace to LLM and get response."""
        system_prompt = self._build_system_prompt()
        builtin_tools = self._build_builtin_tools_prompt()
        react_system = REACT_SYSTEM_PROMPT.format(
            skills_list=self._skills_router_list or "(no tools)",
            max_iterations=self._max_tool_iterations,
            builtin_tools=builtin_tools,
        )
        full_system = f"{system_prompt}\n\n---\n\n{react_system}"

        trace_text = "\n".join(trace) if trace else "(no previous steps)"

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
        """Generate a final answer when max iterations are reached."""
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
        """Build the built-in tools section for the ReAct system prompt."""
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
        """Assemble the full system prompt from persona and skills."""
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
        """Fuzzy-match a skill name returned by the router to a registered skill."""
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

    async def _execute_command(
        self, skill_name: str, command: str
    ) -> str:
        """Execute a shell command from the skill router and return output."""
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
        """Execute a skill by name and return its output."""
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
        """Format skills list with budget-aware tiering."""
        if not skills:
            return ""

        # Tier 1: name + truncated description
        tier1 = self._format_skills_tier1(skills)
        if self._estimate_prompt_size(tier1) <= _PROMPT_BUDGET_TIER1_LIMIT:
            return tier1

        # Tier 2: names only (descriptions dropped)
        tier2 = self._format_skills_tier2(skills)
        if self._estimate_prompt_size(tier2) <= _PROMPT_BUDGET_TIER2_LIMIT:
            logger.info(
                "Prompt budget: Tier 2 (names only) for %d skills", len(skills)
            )
            return tier2

        # Tier 3: binary search for max skills that fit
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
        """Estimate total system prompt size given a skills section."""
        base = len(self._persona_prompt) + 2000  # ReAct template overhead
        builtin = len(self._build_builtin_tools_prompt())
        return base + builtin + len(skills_text)

    def _format_skills_for_router_with_usage(
        self, skills: list[SkillDefinition]
    ) -> str:
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
        """Replace bare python/python3 with a script-local venv python if available."""
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
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}

    def _load_security_config(self) -> dict:
        import yaml
        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            sec = data.get("security", {}) if isinstance(data, dict) else {}
            return sec if isinstance(sec, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}
