"""시스템 프롬프트 YAML 로더와 주요 프롬프트 wiring 회귀 테스트."""

from __future__ import annotations

import json

import pytest

from simpleclaw.agent.asset_selector import SelectorAsset, build_selector_prompt
from simpleclaw.agent.orchestrator import _TOOL_USAGE_INSTRUCTION
from simpleclaw.agent.system_prompts import PromptLoadError, load_system_prompt


def test_load_system_prompt_reads_tool_usage_yaml():
    """tool usage 시스템 프롬프트는 prompts/system YAML에서 로드한다."""
    prompt = load_system_prompt("tool_usage", refresh=True)

    assert prompt.name == "tool_usage"
    assert prompt.source_path.as_posix().endswith("prompts/system/tool_usage.yaml")
    assert "Priority for tool use:" in prompt.prompt
    assert "most specific available tool or skill" in prompt.prompt
    assert "realtime-lookup-skill" not in prompt.prompt
    assert _TOOL_USAGE_INSTRUCTION == prompt.prompt


def test_system_prompt_template_requires_declared_vars():
    """템플릿 필드는 YAML required_vars와 실제 placeholder가 일치해야 한다."""
    prompt = load_system_prompt("asset_selector", refresh=True)

    rendered = prompt.format_field(
        "user_prompt",
        skill_top_k=3,
        recipe_top_k=1,
        user_message="오늘 시장 브리핑해줘",
        asset_manifest_json=json.dumps([{"name": "kr-stock-skill"}], ensure_ascii=False),
    )

    assert "skill_top_k=3" in rendered
    assert "오늘 시장 브리핑해줘" in rendered
    assert "kr-stock-skill" in rendered

    with pytest.raises(PromptLoadError):
        prompt.format_field("user_prompt", skill_top_k=3)


def test_asset_selector_prompt_uses_yaml_template():
    """asset selector user prompt 구성은 prompts/system/asset_selector.yaml 템플릿을 사용한다."""
    rendered = build_selector_prompt(
        user_message="AI 최신 뉴스 정리해줘",
        known_assets=[
            SelectorAsset(
                type="skill",
                name="news-search-skill",
                description="Search recent news",
                trigger="latest news",
            )
        ],
        skill_top_k=2,
        recipe_top_k=0,
    )

    yaml_prompt = load_system_prompt("asset_selector", refresh=True)
    assert rendered.startswith("You are an asset selector for SimpleClaw.")
    assert rendered == yaml_prompt.format_field(
        "user_prompt",
        skill_top_k=2,
        recipe_top_k=0,
        user_message="AI 최신 뉴스 정리해줘",
        asset_manifest_json=json.dumps(
            [
                {
                    "type": "skill",
                    "name": "news-search-skill",
                    "description": "Search recent news",
                    "trigger": "latest news",
                    "commands_count": 0,
                    "parameters_count": 0,
                    "steps_count": 0,
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
    )
