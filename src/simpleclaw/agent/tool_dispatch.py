"""ToolCall 라우팅 dispatch 경계."""

from __future__ import annotations

from typing import Any

from simpleclaw.agent.asset_inventory import handle_asset_inventory
from simpleclaw.agent.config_inspect import handle_config_inspect
from simpleclaw.agent.deploy_status import handle_deploy_status
from simpleclaw.agent.log_debug import handle_log_debug
from simpleclaw.agent.restart_runtime import handle_restart_runtime
from simpleclaw.agent.runtime_status import handle_runtime_status
from simpleclaw.agent.skill_validate import handle_skill_validate
from simpleclaw.agent.skill_learning_tool import handle_skill_learning
from simpleclaw.agent.study_status import handle_study_status
from simpleclaw.agent.builtin_tools import (
    _fetch_search_result_body,
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


def handle_recipe_validate(*args: Any, **kwargs: Any) -> str:
    """recipe_validate handler를 지연 import한다.

    ``simpleclaw.recipes`` import 중 ``agent.tool_dispatch``가 로드되는 순환 경로에서
    operator tool이 recipes package를 다시 import하지 않도록 한다.
    """
    from simpleclaw.agent.recipe_validate import handle_recipe_validate as _handler

    return _handler(*args, **kwargs)


def handle_recipe_generate(*args: Any, **kwargs: Any) -> str:
    """recipe_generate handler를 지연 import한다."""
    from simpleclaw.agent.recipe_generate import handle_recipe_generate as _handler

    return _handler(*args, **kwargs)


async def dispatch_tool_call(
    orchestrator: Any,
    tool_call: ToolCall,
    *,
    operator_tools: bool = False,
    allow_cron_mutation: bool = True,
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
        # 상위 결과 본문을 보강해 snippet-only 환각을 줄인다(BIZ-383 검색 품질).
        return await handle_web_search(args, body_fetcher=_fetch_search_result_body)
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
        return handle_cron_action(
            args,
            orchestrator._cron_scheduler,
            allow_mutation=allow_cron_mutation,
        )
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
    if name == "asset_inventory":
        if not operator_tools:
            return "Error: asset_inventory is available only in operator context."
        return handle_asset_inventory(
            args,
            config_path=orchestrator._config_path,
            skills=orchestrator._skills,
            recipes=getattr(orchestrator, "_recipes", []),
            mcp_manager=getattr(orchestrator, "_mcp_manager", None),
        )
    if name == "deploy_status":
        if not operator_tools:
            return "Error: deploy_status is available only in operator context."
        return handle_deploy_status(args)
    if name == "recipe_validate":
        if not operator_tools:
            return "Error: recipe_validate is available only in operator context."
        return handle_recipe_validate(args, config_path=orchestrator._config_path)
    if name == "recipe_generate":
        if not operator_tools:
            return "Error: recipe_generate is available only in operator context."
        return handle_recipe_generate(
            args,
            config_path=orchestrator._config_path,
            workspace_dir=orchestrator._workspace_dir,
        )
    if name == "skill_validate":
        if not operator_tools:
            return "Error: skill_validate is available only in operator context."
        return handle_skill_validate(
            args,
            config_path=orchestrator._config_path,
            skills=getattr(orchestrator, "_skills", None),
        )
    if name == "restart_runtime":
        if not operator_tools:
            return "Error: restart_runtime is available only in operator context."
        return handle_restart_runtime(
            args,
            config_path=orchestrator._config_path,
            scheduler=orchestrator._cron_scheduler,
        )
    if name == "skill_learning":
        if not operator_tools:
            return "Error: skill_learning is available only in operator context."
        return handle_skill_learning(
            args,
            config=orchestrator._skill_learning_config,
            skills_config=orchestrator._skills_config,
        )
    if name == "study_status":
        if not operator_tools:
            return "Error: study_status is available only in operator context."
        return handle_study_status(args, config_path=orchestrator._config_path)
    if name == "clarify":
        return handle_clarify(
            args,
            orchestrator._pending_clarify,
            chat_id=clarify_chat_id_var.get(),
        )
    return f"Error: unknown tool '{name}'."
