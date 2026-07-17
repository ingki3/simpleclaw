"""LLM routing policy config 로드 테스트 (BIZ-448).

`llm.fallback` / `llm.multimodal` 키가 로드되고, 미지정 시 None 기본값이
유지되는지 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.config_sections.llm import load_llm_config


def test_load_llm_config_preserves_fallback_and_multimodal(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
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
""".strip(),
        encoding="utf-8",
    )

    llm = load_llm_config(config)

    assert llm["default"] == "openrouter_glm_5_2"
    assert llm["fallback"] == "gemini"
    assert llm["multimodal"] == "gemini"


def test_load_llm_config_defaults_fallback_and_multimodal_to_none(tmp_path: Path):
    """기존 config(정책 키 없음)는 None 기본값으로 로드 — 회귀 0."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
  default: gemini
  providers:
    gemini:
      type: api
      model: gemini-3.5-flash
      api_key: test-gemini-key
""".strip(),
        encoding="utf-8",
    )

    llm = load_llm_config(config)

    assert llm["default"] == "gemini"
    assert llm["fallback"] is None
    assert llm["multimodal"] is None


def test_load_llm_config_missing_file_returns_policy_defaults(tmp_path: Path):
    llm = load_llm_config(tmp_path / "nonexistent.yaml")

    assert llm["fallback"] is None
    assert llm["multimodal"] is None
