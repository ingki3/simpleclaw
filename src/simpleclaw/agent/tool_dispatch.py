"""ToolCall 라우팅 dispatch 경계."""

from __future__ import annotations

from typing import Any

from simpleclaw.agent.config_inspect import handle_config_inspect
from simpleclaw.agent.log_debug import handle_log_debug
from simpleclaw.agent.runtime_status import handle_runtime_status
from simpleclaw.agent.builtin_tools import (
    handle_clarify,
    handle_cron_action,
    handle_file_manage,
    handle_file_read,
    handle_file_write,
    handle_skill_docs,
    handle_web_fetch,
    handle_web_search,
)
from simpleclaw.agent.clarify import clarify_chat_id_var
from simpleclaw.llm.models import ToolCall


async def dispatch_tool_call(
    orchestrator: Any, tool_call: ToolCall, *, operator_tools: bool = False
) -> str:
    """ToolCall을 적절한 핸들러로 라우팅하여 실행 결과를 반환한다."""
    name = tool_call.name
    args = tool_call.arguments

    if name == "execute_skill":
        return await orchestrator._dispatch_external_skill(args)
    if name == "cli":
        cmd = args.get("command", "")
        if not cmd:
            return "Error: 'command' argument is required."
        return await orchestrator._execute_command("cli", cmd)
    if name == "web_fetch":
        return await handle_web_fetch(args, headless_binary=orchestrator._headless_binary)
    if name == "web_search":
        return await handle_web_search(args)
    if name == "file_read":
        return handle_file_read(
            args,
            orchestrator._workspace_dir,
            persona_local_dir=orchestrator._persona_config["local_dir"],
        )
    if name == "file_write":
        return handle_file_write(args, orchestrator._workspace_dir)
    if name == "file_manage":
        return handle_file_manage(
            args,
            orchestrator._workspace_dir,
            persona_local_dir=orchestrator._persona_config["local_dir"],
        )
    if name == "skill_docs":
        return handle_skill_docs(args, orchestrator._skills_by_name)
    if name == "search_memory":
        return await orchestrator._search_memory(args)
    if name == "cron":
        return handle_cron_action(args, orchestrator._cron_scheduler)
    if name == "runtime_status":
        if not operator_tools:
            return "Error: runtime_status is available only in operator context."
        return handle_runtime_status(
            args,
            config_path=orchestrator._config_path,
            scheduler=orchestrator._cron_scheduler,
        )
    if name == "config_inspect":
        if not operator_tools:
            return "Error: config_inspect is available only in operator context."
        return handle_config_inspect(args, config_path=orchestrator._config_path)
    if name == "log_debug":
        if not operator_tools:
            return "Error: log_debug is available only in operator context."
        return handle_log_debug(args)
    if name == "clarify":
        return handle_clarify(
            args,
            orchestrator._pending_clarify,
            chat_id=clarify_chat_id_var.get(),
        )
    return f"Error: unknown tool '{name}'."
