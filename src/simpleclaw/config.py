"""SimpleClaw 설정 로더 facade.

기존 ``from simpleclaw.config import ...`` import 호환성을 유지하면서, 실제
loader/coercer 구현은 subsystem별 ``simpleclaw.config_sections`` 모듈로 분리한다.
config.yaml schema와 public function/default 이름은 변경하지 않는다.
"""

from __future__ import annotations

from simpleclaw.config_sections.agents import (
    _AGENT_DEFAULTS,
    _ASSET_SELECTION_DEFAULTS,
    _DEFAULTS,
    _RECIPES_DEFAULTS,
    _SKILL_LEARNING_DEFAULTS,
    _SUB_AGENTS_DEFAULTS,
    _agent_with_defaults,
    _coerce_float_config,
    _coerce_int_config,
    load_agent_config,
    load_asset_selection_config,
    load_persona_config,
    load_recipe_learning_config,
    load_recipes_config,
    load_skills_learning_config,
    load_sub_agents_config,
)
from simpleclaw.config_sections.channels import (
    _ADMIN_API_DEFAULTS,
    _TELEGRAM_DEFAULTS,
    _TELEGRAM_STREAMING_DEFAULTS,
    _VOICE_DEFAULTS,
    _WEBHOOK_DEFAULTS,
    _admin_api_with_defaults,
    _coerce_streaming_config,
    load_admin_api_config,
    load_telegram_config,
    load_voice_config,
    load_webhook_config,
)
from simpleclaw.config_sections.common import _resolve_secret_field
from simpleclaw.config_sections.daemon import (
    _DAEMON_DEFAULTS,
    _clamped_float,
    _coerce_archive_after_days,
    _coerce_context_cron,
    _coerce_default_ttl_days,
    _coerce_dreaming_max_tokens,
    _coerce_language_policy,
    _coerce_proactive_policy,
    _positive_int,
    load_daemon_config,
)
from simpleclaw.config_sections.llm import _LLM_DEFAULTS, load_llm_config
from simpleclaw.config_sections.mcp import _MCP_DEFAULTS, load_mcp_config
from simpleclaw.config_sections.memory import _MEMORY_DEFAULTS, load_memory_config
from simpleclaw.config_sections.review import _REVIEW_DEFAULTS, load_review_config
from simpleclaw.config_sections.security import load_security_config
from simpleclaw.config_sections.study import _STUDY_DEFAULTS, load_study_config

__all__ = [
    "_ADMIN_API_DEFAULTS",
    "_AGENT_DEFAULTS",
    "_ASSET_SELECTION_DEFAULTS",
    "_DAEMON_DEFAULTS",
    "_DEFAULTS",
    "_LLM_DEFAULTS",
    "_MCP_DEFAULTS",
    "_MEMORY_DEFAULTS",
    "_RECIPES_DEFAULTS",
    "_REVIEW_DEFAULTS",
    "_SKILL_LEARNING_DEFAULTS",
    "_STUDY_DEFAULTS",
    "_SUB_AGENTS_DEFAULTS",
    "_TELEGRAM_DEFAULTS",
    "_TELEGRAM_STREAMING_DEFAULTS",
    "_VOICE_DEFAULTS",
    "_WEBHOOK_DEFAULTS",
    "_admin_api_with_defaults",
    "_agent_with_defaults",
    "_clamped_float",
    "_coerce_archive_after_days",
    "_coerce_context_cron",
    "_coerce_default_ttl_days",
    "_coerce_dreaming_max_tokens",
    "_coerce_float_config",
    "_coerce_int_config",
    "_coerce_language_policy",
    "_coerce_proactive_policy",
    "_coerce_streaming_config",
    "_positive_int",
    "_resolve_secret_field",
    "load_admin_api_config",
    "load_agent_config",
    "load_asset_selection_config",
    "load_daemon_config",
    "load_llm_config",
    "load_mcp_config",
    "load_memory_config",
    "load_persona_config",
    "load_recipe_learning_config",
    "load_recipes_config",
    "load_review_config",
    "load_security_config",
    "load_skills_learning_config",
    "load_study_config",
    "load_sub_agents_config",
    "load_telegram_config",
    "load_voice_config",
    "load_webhook_config",
]
