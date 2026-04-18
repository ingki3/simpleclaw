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

from simpleclaw.config import load_agent_config, load_persona_config
from simpleclaw.llm.models import LLMRequest
from simpleclaw.llm.router import create_router
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.models import ConversationMessage, MessageRole
from simpleclaw.persona.assembler import assemble_prompt
from simpleclaw.persona.resolver import resolve_persona_files
from simpleclaw.skills.discovery import discover_skills
from simpleclaw.skills.executor import execute_skill
from simpleclaw.skills.models import SkillDefinition

logger = logging.getLogger(__name__)

_SKILL_ROUTER_PROMPT = """\
You are a skill router. Given a user message and a list of available skills, \
decide if any skill should be executed to answer the user's question.

## Available Skills
{skills_list}

## Rules
- If the user's message can be answered with your own knowledge, respond: {{"use_skill": false}}
- If a skill is needed, respond with the EXACT shell command to execute: \
{{"use_skill": true, "skill_name": "<name>", "command": "<full shell command to run>"}}
- For the command, refer to the skill's usage instructions and construct the appropriate CLI command.
- Match skills by their name, description, and trigger conditions.
- Only select a skill if it CLEARLY matches the user's intent.
- Respond ONLY with valid JSON, nothing else.

## User Message
{user_message}"""

_SKILL_RESULT_CONTEXT = """\
## Skill Execution Result

The skill **{skill_name}** was executed with the following result:

```
{result}
```

Use this result to formulate your response to the user. \
Summarize or format the result naturally."""


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

        logger.info(
            "AgentOrchestrator initialized: persona=%d chars, skills=%d, backend=%s",
            len(self._persona_prompt),
            len(self._skills),
            self._router.get_default_backend(),
        )

    async def process_message(
        self, text: str, user_id: int, chat_id: int
    ) -> str:
        """Process an incoming message through the full agent pipeline.

        1. Skill routing: decide if a skill is needed
        2. If needed: execute skill
        3. Generate response with full context
        4. Store conversation
        """
        # Step 1: Skill routing
        skill_result = None
        skill_name = None

        if self._skills:
            routing = await self._route_to_skill(text)
            if routing and routing.get("use_skill"):
                skill_name = routing.get("skill_name", "")
                command = routing.get("command", "")
                logger.info(
                    "Skill router selected: %s (command: %s)",
                    skill_name, command,
                )

                # Step 2: Execute skill
                if command:
                    skill_result = await self._execute_command(
                        skill_name, command
                    )
                else:
                    skill_args = routing.get("args", "")
                    skill_result = await self._execute_skill(
                        skill_name, skill_args
                    )

        # Step 3: Generate response
        response_text = await self._generate_response(
            text, skill_name, skill_result
        )

        # Step 4: Store conversation
        self._store.add_message(ConversationMessage(
            role=MessageRole.USER, content=text,
        ))
        self._store.add_message(ConversationMessage(
            role=MessageRole.ASSISTANT, content=response_text,
        ))

        return response_text

    async def _route_to_skill(self, user_message: str) -> dict | None:
        """Ask LLM whether a skill should be used for this message."""
        if not self._skills_router_list:
            return None

        prompt = _SKILL_ROUTER_PROMPT.format(
            skills_list=self._skills_router_list_with_usage,
            user_message=user_message,
        )

        try:
            request = LLMRequest(
                system_prompt=(
                    "You are a JSON-only skill router. "
                    "Respond with valid JSON only."
                ),
                user_message=prompt,
            )
            response = await self._router.send(request)
            raw = response.text.strip()

            # Extract JSON from response (handle markdown code blocks)
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            result = json.loads(raw)
            logger.info("Skill routing result: %s", result)
            return result

        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Skill routing failed: %s", exc)
            return None

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

        logger.info("Executing skill command: %s", command)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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

    async def _generate_response(
        self,
        user_message: str,
        skill_name: str | None,
        skill_result: str | None,
    ) -> str:
        """Generate the final response with full context."""
        # Build system prompt
        system_prompt = self._build_system_prompt()

        # Add skill result to context if available
        if skill_name and skill_result:
            system_prompt += "\n\n" + _SKILL_RESULT_CONTEXT.format(
                skill_name=skill_name,
                result=skill_result[:3000],
            )

        # Build conversation history
        recent = self._store.get_recent(limit=self._history_limit)
        messages = [
            {"role": msg.role.value, "content": msg.content}
            for msg in recent
        ]
        messages.append({"role": "user", "content": user_message})

        try:
            request = LLMRequest(
                system_prompt=system_prompt,
                user_message=user_message,
                messages=messages,
            )
            response = await self._router.send(request)
            logger.info(
                "Agent response (%s): %d chars",
                response.backend_name,
                len(response.text),
            )
            return response.text
        except Exception as exc:
            logger.error("LLM error: %s", exc)
            return f"죄송합니다, 오류가 발생했습니다: {str(exc)[:200]}"

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

    def _load_skills_config(self) -> dict:
        """Load skills config section from config.yaml."""
        import yaml

        try:
            with open(self._config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("skills", {}) if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError):
            return {}
