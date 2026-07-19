"""Router static provider alias 테스트 (BIZ-448).

`provider: openai` alias 로 `openrouter_glm_5_2` 같은 커스텀 백엔드 이름이
OpenAIProvider 구현으로 등록되는지 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.llm.providers.openai_provider import OpenAIProvider
from simpleclaw.llm.router import create_router

_ALIAS_CONFIG = """
llm:
  default: openrouter_glm_5_2
  fallback: gemini
  multimodal: gemini
  providers:
    openrouter_glm_5_2:
      provider: openai
      type: api
      model: z-ai/glm-5.2
      api_key: test-openrouter-key
      base_url: https://openrouter.ai/api/v1
      extra_body:
        reasoning:
          enabled: false
    gemini:
      type: api
      model: gemini-3.5-flash
      api_key: test-gemini-key
""".strip()


def test_create_router_uses_provider_alias_for_openrouter_backend(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(_ALIAS_CONFIG, encoding="utf-8")

    router = create_router(config)

    assert router.get_default_backend() == "openrouter_glm_5_2"
    assert "openrouter_glm_5_2" in router.list_backends()
    assert "gemini" in router.list_backends()
    assert router._backends["openrouter_glm_5_2"].transport == "openai_chat"
    assert router._backends["openrouter_glm_5_2"].profile == "openai"


def test_create_router_alias_backend_is_openai_provider_with_extra_body(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(_ALIAS_CONFIG, encoding="utf-8")

    router = create_router(config)

    provider = router._providers["openrouter_glm_5_2"]
    assert isinstance(provider, OpenAIProvider)
    assert provider._name == "openrouter_glm_5_2"
    assert provider._extra_body == {"reasoning": {"enabled": False}}
    assert router.get_backend_profile("openrouter_glm_5_2").name == "openai"


def test_create_router_wires_fallback_and_multimodal(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(_ALIAS_CONFIG, encoding="utf-8")

    router = create_router(config)

    assert router.get_fallback_backend() == "gemini"
    assert router.get_multimodal_backend() == "gemini"


def test_create_router_disables_unavailable_policy_backends(tmp_path: Path):
    """가용 provider 가 없는 fallback/multimodal 이름은 None 으로 무력화."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: gemini
  fallback: nonexistent_backend
  multimodal: another_missing
  providers:
    gemini:
      type: api
      model: gemini-3.5-flash
      api_key: test-gemini-key
""".strip(),
        encoding="utf-8",
    )

    router = create_router(config)

    assert router.get_fallback_backend() is None
    assert router.get_multimodal_backend() is None
