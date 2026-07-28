"""BIZ-507 — 등록 skill 전용 secret reference loader 보안 계약 테스트."""

from __future__ import annotations

import logging
import os

import pytest

from simpleclaw.security.skill_env import (
    SkillEnvConfigError,
    load_skill_env_secret_refs,
)


class _FakeSecretsManager:
    """실제 vault를 건드리지 않고 reference 해소 결과만 제어한다."""

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, reference: str) -> str:
        return self._values.get(reference, "")


def test_loads_exact_skill_child_env_without_mutating_parent(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    manager = _FakeSecretsManager({"file:gemini_api_key": "skill-secret-canary"})

    resolved = load_skill_env_secret_refs(
        {
            "news-search-skill": {
                "GEMINI_API_KEY": "file:gemini_api_key",
            }
        },
        manager=manager,
    )

    assert resolved == {
        "news-search-skill": {"GEMINI_API_KEY": "skill-secret-canary"}
    }
    assert "GEMINI_API_KEY" not in os.environ


@pytest.mark.parametrize(
    "raw_config",
    [
        [],
        {"": {"GEMINI_API_KEY": "file:key"}},
        {"news-search-skill": []},
        {"news-search-skill": {"gemini_api_key": "file:key"}},
        {"news-search-skill": {"GEMINI-API-KEY": "file:key"}},
        {"news-search-skill": {"GEMINI_API_KEY;echo": "file:key"}},
        {"news-search-skill": {"GEMINI_API_KEY": 123}},
        {"news-search-skill": {"GEMINI_API_KEY": "plaintext-secret"}},
        {"news-search-skill": {"GEMINI_API_KEY": "plain:plaintext-secret"}},
    ],
)
def test_rejects_malformed_or_plaintext_config(raw_config):
    with pytest.raises(SkillEnvConfigError):
        load_skill_env_secret_refs(raw_config, manager=_FakeSecretsManager({}))


def test_unresolved_reference_fails_closed_without_secret_value(caplog):
    canary = "unresolved-secret-canary"

    with caplog.at_level(logging.DEBUG), pytest.raises(
        SkillEnvConfigError
    ) as caught:
        load_skill_env_secret_refs(
            {
                "news-search-skill": {
                    "GEMINI_API_KEY": "file:missing_key",
                }
            },
            manager=_FakeSecretsManager({}),
        )

    serialized = f"{caught.value}\n{caplog.text}"
    assert canary not in serialized
    assert "missing_key" not in serialized
    assert "Unable to resolve configured skill secret" in str(caught.value)


def test_resolved_secret_is_never_logged(caplog):
    canary = "resolved-secret-canary"

    with caplog.at_level(logging.DEBUG):
        resolved = load_skill_env_secret_refs(
            {
                "news-search-skill": {
                    "GEMINI_API_KEY": "file:gemini_api_key",
                }
            },
            manager=_FakeSecretsManager({"file:gemini_api_key": canary}),
        )

    assert resolved["news-search-skill"]["GEMINI_API_KEY"] == canary
    assert canary not in caplog.text
