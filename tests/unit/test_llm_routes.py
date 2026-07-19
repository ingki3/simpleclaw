"""Named LLM route resolution and legacy config normalization."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from simpleclaw.config_sections.llm import load_llm_config
from simpleclaw.llm.models import LLMConfigError, LLMRequest, LLMResponse, LLMRoute
from simpleclaw.llm.providers.base import LLMProvider
from simpleclaw.llm.router import LLMRouter, create_router


class _Provider(LLMProvider):
    async def send(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def __init__(self, name: str, *, error: Exception | None = None):
        result = LLMResponse(text="ok", backend_name=name, model="m")
        self.send = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
        self.stream = AsyncMock(return_value=result)


def _router(*, primary_error: Exception | None = None) -> LLMRouter:
    primary = _Provider("analysis", error=primary_error)
    retry = _Provider("retry")
    return LLMRouter(
        backends={},
        providers={"analysis": primary, "retry": retry},
        default_backend="analysis",
        routes={
            "default": LLMRoute("default", "analysis"),
            "turn_analysis": LLMRoute("turn_analysis", "analysis", "retry"),
        },
    )


@pytest.mark.asyncio
async def test_route_resolves_primary_backend():
    response = await _router().send(
        LLMRequest(route_name="turn_analysis", user_message="hi")
    )
    assert response.backend_name == "analysis"


@pytest.mark.asyncio
async def test_route_retries_configured_backend_once():
    router = _router(primary_error=RuntimeError("down"))
    response = await router.send(LLMRequest(route_name="turn_analysis"))
    assert response.backend_name == "retry"


@pytest.mark.asyncio
async def test_explicit_backend_keeps_no_retry_contract():
    router = _router(primary_error=RuntimeError("down"))
    with pytest.raises(RuntimeError, match="down"):
        await router.send(LLMRequest(backend_name="analysis"))


@pytest.mark.asyncio
async def test_dual_selector_and_unknown_route_fail_fast():
    router = _router()
    with pytest.raises(LLMConfigError, match="both backend_name and route_name"):
        await router.send(LLMRequest(backend_name="analysis", route_name="default"))
    with pytest.raises(LLMConfigError, match="Unknown LLM route"):
        await router.send(LLMRequest(route_name="missing"))


def test_new_routes_override_legacy_policy(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: legacy
  fallback: legacy_retry
  routes:
    default: {primary: modern, retry: modern_retry}
    turn_analysis: {primary: analysis, retry: analysis_retry}
  providers: {}
""".strip(),
        encoding="utf-8",
    )
    loaded = load_llm_config(config)
    assert loaded["routes"]["default"] == {
        "primary": "modern",
        "retry": "modern_retry",
    }
    assert loaded["routes"]["turn_analysis"] == {
        "primary": "analysis",
        "retry": "analysis_retry",
    }


def test_legacy_turn_analysis_model_becomes_static_backend_and_route(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-default
      api_key: test
agent:
  turn_analysis:
    provider: gemini
    model: gemini-analysis
""".strip(),
        encoding="utf-8",
    )
    loaded = load_llm_config(config)
    target = loaded["routes"]["turn_analysis"]["primary"]
    assert target == "__legacy_turn_analysis_primary"
    assert loaded["providers"][target]["model"] == "gemini-analysis"


def test_router_does_not_expose_dynamic_model_backend_clone():
    assert not hasattr(_router(), "ensure_model_backend")


def test_legacy_routes_follow_initialized_default_when_legacy_provider_is_unavailable(
    tmp_path, caplog, monkeypatch
):
    from simpleclaw.llm import router as router_module

    original_get_transport_class = router_module.get_transport_class

    def fail_legacy_transport(name):
        if name == "openai_chat":
            raise LLMConfigError("legacy transport initialization failed")
        return original_get_transport_class(name)

    monkeypatch.setattr(router_module, "get_transport_class", fail_legacy_transport)
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: unavailable_legacy
  providers:
    unavailable_legacy: {type: api, transport: openai, model: old}
    available: {type: cli, command: echo}
agent:
  turn_analysis:
    backend: unavailable_legacy
""".strip(),
        encoding="utf-8",
    )

    router = create_router(config)

    assert router.get_default_backend() == "available"
    assert router.get_route("default").primary == "available"
    assert router.get_route("turn_analysis").primary == "available"
    assert "Legacy LLM route 'turn_analysis' primary backend 'unavailable_legacy'" in caplog.text


def test_explicit_route_with_unavailable_backend_fails_during_startup(
    tmp_path, monkeypatch
):
    from simpleclaw.llm import router as router_module

    original_get_transport_class = router_module.get_transport_class

    def fail_unavailable_transport(name):
        if name == "openai_chat":
            raise LLMConfigError("unavailable transport initialization failed")
        return original_get_transport_class(name)

    monkeypatch.setattr(
        router_module, "get_transport_class", fail_unavailable_transport
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: available
  routes:
    default: available
    turn_analysis: unavailable
  providers:
    unavailable: {type: api, transport: openai, model: old}
    available: {type: cli, command: echo}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(LLMConfigError, match="turn_analysis.*unavailable"):
        create_router(config)
