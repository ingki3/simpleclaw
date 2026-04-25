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
import re
from pathlib import Path

from simpleclaw.config import load_agent_config, load_persona_config
from simpleclaw.llm.models import LLMRequest
from simpleclaw.recipes.loader import discover_recipes, load_recipe
from simpleclaw.recipes.models import RecipeDefinition
from simpleclaw.llm.router import create_router
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
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

logger = logging.getLogger(__name__)

_REACT_SYSTEM_PROMPT = """\
You are an AI agent that solves tasks step-by-step using available tools.
Use the Thought / Action / Observation cycle.

## Output Format

When you need to use a tool:
```
Thought: <your reasoning about what to do next>
Action: {{"skill_name": "<name>", "command": "<shell command>"}}
```

When you have enough information to give the final answer:
```
Thought: <why the task is complete>
Answer: <your response to the user>
```

## Rules
- Evaluate each Observation critically. If information is incomplete or partial, \
take another Action with a different query.
- Do NOT fabricate information. Only include facts from Observations.
- If a tool fails, try a different approach or query.
- **If ALL tool attempts fail, honestly tell the user the tool failed. \
NEVER generate fake data from your training knowledge as a substitute.**
- Maximum {max_iterations} tool calls allowed.
- Respond in the same language as the user.
- **CRITICAL: Only use commands EXACTLY as shown in the skill's usage instructions. \
Copy the command template verbatim and only change the arguments. \
NEVER invent file paths, script names, or modify the executable path.**
- If a skill's usage shows `/path/to/venv/bin/python /path/to/script.py`, \
use that EXACT path — do not substitute or guess alternative paths.

## Available Tools
{skills_list}"""

_REACT_USER_PROMPT = """\
{datetime_context}
{react_trace}
## User Request
{user_message}"""


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

    async def process_cron_message(self, text: str) -> str:
        """Process a cron job message with isolated context.

        Uses the ReAct loop but does NOT load conversation history
        and does NOT store messages in the shared conversation DB.
        """
        return await self._react_loop(text, isolated=True)

    async def process_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str:
        """Process an incoming message through the ReAct pipeline.

        Handles:
        0. /recipe-name commands → execute recipe
        1. Normal messages → ReAct loop
        """
        # Check for /recipe-name commands
        recipe_result = await self._try_recipe_command(text)
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

        if command:
            result = await self._execute_command(skill_name, command)
        else:
            skill_args = routing.get("args", "")
            result = await self._execute_skill(skill_name, skill_args)

        return skill_name, result

    # ------------------------------------------------------------------
    # ReAct loop
    # ------------------------------------------------------------------

    async def _react_loop(
        self, text: str, isolated: bool = False
    ) -> str:
        """Execute the ReAct (Reasoning + Acting) loop.

        The LLM reasons about the task (Thought), takes actions (Action),
        observes results (Observation), and repeats until it produces
        a final Answer.

        Args:
            text: User message.
            isolated: If True, skip conversation history (for cron jobs).
        """
        trace: list[str] = []

        for i in range(self._max_tool_iterations):
            raw = await self._react_step(text, trace, isolated)
            thought, action, answer = self._parse_react(raw)

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
                # No Action or Answer parsed → treat entire response as answer
                logger.info("ReAct [%d] fallback: raw response", i + 1)
                return raw

        # Max iterations reached → generate final answer from trace
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
        react_system = _REACT_SYSTEM_PROMPT.format(
            skills_list=self._skills_router_list_with_usage or "(no tools)",
            max_iterations=self._max_tool_iterations,
        )
        full_system = f"{system_prompt}\n\n---\n\n{react_system}"

        trace_text = "\n".join(trace) if trace else "(no previous steps)"

        # Inject current datetime so LLM knows "today"
        from datetime import datetime, timezone, timedelta
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        datetime_context = now_kst.strftime(
            "[현재 시각: %Y-%m-%d %H:%M (%A) KST]"
        )

        user_prompt = _REACT_USER_PROMPT.format(
            datetime_context=datetime_context,
            react_trace=trace_text,
            user_message=user_message,
        )

        # Build messages with optional conversation history
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

    @staticmethod
    def _parse_react(
        response: str,
    ) -> tuple[str | None, dict | None, str | None]:
        """Parse a ReAct response into (thought, action, answer).

        Returns:
            thought: The Thought text, or None.
            action: Parsed Action JSON dict, or None.
            answer: The Answer text, or None.
        """
        thought = None
        action = None
        answer = None

        # Extract Thought
        thought_match = re.search(
            r"Thought:\s*(.+?)(?=\nAction:|\nAnswer:|\Z)",
            response, re.DOTALL,
        )
        if thought_match:
            thought = thought_match.group(1).strip()

        # Extract Answer
        answer_match = re.search(r"Answer:\s*(.+)", response, re.DOTALL)
        if answer_match:
            answer = answer_match.group(1).strip()
            return thought, None, answer

        # Extract Action (JSON)
        action_match = re.search(r"Action:\s*(\{.+?\})", response, re.DOTALL)
        if action_match:
            try:
                action = json.loads(action_match.group(1))
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse Action JSON: %s",
                    action_match.group(1)[:200],
                )

        return thought, action, answer

    def _resolve_skill_name(self, name: str) -> SkillDefinition | None:
        """Fuzzy-match a skill name returned by the router to a registered skill."""
        # Exact match
        if name in self._skills_by_name:
            return self._skills_by_name[name]

        # Case-insensitive match
        lower = name.lower()
        for key, skill in self._skills_by_name.items():
            if key.lower() == lower:
                return skill

        # Normalize: "Gmail Skill" → "gmail-skill"
        normalized = lower.replace(" ", "-")
        for key, skill in self._skills_by_name.items():
            if key.lower() == normalized:
                return skill

        # Partial match: "gmail" matches "gmail-skill"
        for key, skill in self._skills_by_name.items():
            if lower.replace("-", "").replace(" ", "") in key.lower().replace("-", ""):
                return skill

        return None

    async def _execute_command(
        self, skill_name: str, command: str
    ) -> str:
        """Execute a shell command from the skill router and return output."""
        import asyncio

        # Security: check for dangerous commands
        try:
            self._command_guard.check(command)
        except DangerousCommandError as exc:
            logger.warning("Command blocked for skill '%s': %s", skill_name, exc)
            return f"Command blocked (dangerous pattern detected): {exc.description}"

        # Fix bare "python"/"python3" → use script's local venv if available
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
            # Skill has no executable script — read SKILL.md for context
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

    def _format_skills_for_prompt(self, skills: list[SkillDefinition]) -> str:
        """Format skills as a compact list for the system prompt."""
        if not skills:
            return ""

        lines = ["## Available Skills", ""]
        for skill in skills:
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    def _format_skills_for_router(self, skills: list[SkillDefinition]) -> str:
        """Format skills with details for the skill routing prompt."""
        if not skills:
            return ""

        lines = []
        for skill in skills:
            entry = f"- name: {skill.name}\n  description: {skill.description}"
            if skill.trigger:
                entry += f"\n  trigger: {skill.trigger}"
            lines.append(entry)
        return "\n".join(lines)

    def _format_skills_for_router_with_usage(
        self, skills: list[SkillDefinition]
    ) -> str:
        """Format skills with SKILL.md usage instructions for the router."""
        if not skills:
            return ""

        entries = []
        for skill in skills:
            entry = f"### {skill.name}\n{skill.description}\n"

            # Include SKILL.md usage if available
            skill_md = Path(skill.skill_dir) / "SKILL.md"
            if skill_md.is_file():
                try:
                    content = skill_md.read_text(encoding="utf-8")
                    # Extract usage section (keep it concise)
                    if len(content) > 800:
                        content = content[:800] + "..."
                    entry += f"\n{content}\n"
                except OSError:
                    pass

            entries.append(entry)
        return "\n---\n".join(entries)

    @staticmethod
    def _fix_python_path(command: str) -> str:
        """Replace bare python/python3 with a script-local venv python if available.

        When LLM generates 'python script.py' or 'python3 script.py',
        check if a venv exists near the script and use its python instead.
        """
        import shlex

        parts = command.split(None, 1)
        if len(parts) < 2:
            return command

        interpreter, rest = parts[0], parts[1]

        # Only handle bare python/python3 (not full paths like /usr/bin/python3)
        if interpreter not in ("python", "python3"):
            return command

        # Find the script path in the rest of the command
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
            # Fallback: at least use python3
            if interpreter == "python":
                return f"python3 {rest}"
            return command

        # Look for venv near the script
        for venv_dir in (
            script_path.parent / "venv",
            script_path.parent.parent / "venv",
            script_path.parent / ".venv",
            script_path.parent.parent / ".venv",
        ):
            venv_python = venv_dir / "bin" / "python"
            if venv_python.is_file():
                return f"{venv_python} {rest}"

        # No venv found, use python3
        if interpreter == "python":
            return f"python3 {rest}"
        return command

    # ------------------------------------------------------------------
    # Recipe commands (/recipe-name)
    # ------------------------------------------------------------------

    async def _try_recipe_command(self, text: str) -> str | None:
        """Check if text is a /recipe-name command and execute it.

        Discovers recipes from .agent/recipes/ on every call so new
        recipes are available without restart.

        Returns the response text, or None if not a recipe command.
        """
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped[1:].split(None, 1)
        if not parts:
            return None

        cmd_name = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        # Discover recipes fresh from disk
        recipes = discover_recipes(".agent/recipes")
        recipes_by_name = {r.name: r for r in recipes}

        recipe = recipes_by_name.get(cmd_name)
        if recipe is None:
            return None

        logger.info("Recipe command: /%s", cmd_name)

        # Parse key=value parameters
        params = {}
        if rest:
            for match in re.finditer(r'(\w+)=(?:"([^"]*)"|(\S+))', rest):
                key = match.group(1)
                value = match.group(2) if match.group(2) is not None else match.group(3)
                params[key] = value

        # Render instructions with parameter substitution
        if recipe.instructions:
            rendered = recipe.instructions
            # Apply defaults for missing params
            for p in recipe.parameters:
                if p.name not in params and p.default:
                    params[p.name] = p.default

            # Substitute {{ var }} and ${var}
            def jinja_replacer(match):
                key = match.group(1).strip()
                return params.get(key, match.group(0))

            rendered = re.sub(r"\{\{\s*(\w+)\s*\}\}", jinja_replacer, rendered)

            def shell_replacer(match):
                key = match.group(1)
                return params.get(key, match.group(0))

            rendered = re.sub(r"\$\{(\w+)\}", shell_replacer, rendered)

            # Process rendered instructions through ReAct pipeline
            return await self._react_loop(rendered)

        # No instructions — fallback to step-based execution
        return f"레시피 '{cmd_name}'에 instructions가 정의되어 있지 않습니다."

    def _load_skills_config(self) -> dict:
        """Load skills config section from config.yaml."""
        import yaml

        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}

    def _load_security_config(self) -> dict:
        """Load security config section from config.yaml."""
        import yaml

        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            sec = data.get("security", {}) if isinstance(data, dict) else {}
            return sec if isinstance(sec, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}
