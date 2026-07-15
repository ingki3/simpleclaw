"""BIZ-443 — 공통 subprocess env scrub 정책 회귀 테스트.

``filter_env``가 provider/gateway/admin/token 성격의 환경변수를 기본 제거하고,
baseline 키는 유지하며, 명시 passthrough 만 통과시키는지 고정한다.
기존 ``test_env_filter.py``는 초기 블록리스트만 다루므로, BIZ-443에서 확장된
패턴(provider prefix, 범용 시크릿 suffix, admin API)을 별도 파일로 고정한다.
"""

import os
from unittest.mock import patch

from simpleclaw.security.env_filter import filter_env

_BASELINE = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/user",
    "LANG": "en_US.UTF-8",
    "USER": "testuser",
    "TERM": "xterm-256color",
}


def _filtered(**extra) -> dict:
    env = dict(_BASELINE)
    env.update(extra)
    with patch.dict(os.environ, env, clear=True):
        return filter_env()


class TestProviderPrefixScrub:
    def test_llm_provider_prefixes_removed(self):
        result = _filtered(
            GEMINI_API_KEY="AIza-x",
            GEMINI_MODEL="gemini-2.5-pro",
            AZURE_OPENAI_ENDPOINT="https://x.openai.azure.com",
            OPENROUTER_BASE_URL="https://openrouter.ai/api",
            MISTRAL_WORKSPACE="w",
            COHERE_REGION="us",
            DEEPSEEK_ORG="o",
            GROQ_REGION="us",
            XAI_ORG="o",
            HF_HUB_OFFLINE="1",
            HUGGINGFACE_HUB_CACHE="/tmp/hf",
            CLAUDE_CONFIG_DIR="/tmp/claude",
        )
        for key in (
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "AZURE_OPENAI_ENDPOINT",
            "OPENROUTER_BASE_URL",
            "MISTRAL_WORKSPACE",
            "COHERE_REGION",
            "DEEPSEEK_ORG",
            "GROQ_REGION",
            "XAI_ORG",
            "HF_HUB_OFFLINE",
            "HUGGINGFACE_HUB_CACHE",
            "CLAUDE_CONFIG_DIR",
        ):
            assert key not in result, key

    def test_admin_api_keys_removed(self):
        result = _filtered(
            ADMIN_API_TOKEN="tok",
            ADMIN_API_BASE="http://127.0.0.1:8082",
        )
        assert "ADMIN_API_TOKEN" not in result
        assert "ADMIN_API_BASE" not in result


class TestGenericSecretSuffixScrub:
    def test_generic_credential_suffixes_removed(self):
        result = _filtered(
            MY_APIKEY="x",
            SOME_SERVICE_CREDENTIALS="/path/creds.json",
            OBJECT_STORE_ACCESS_KEY="AKIA-x",
            SIGNING_PRIVATE_KEY="-----BEGIN",
            DB_PASSWD="pw",
        )
        for key in (
            "MY_APIKEY",
            "SOME_SERVICE_CREDENTIALS",
            "OBJECT_STORE_ACCESS_KEY",
            "SIGNING_PRIVATE_KEY",
            "DB_PASSWD",
        ):
            assert key not in result, key


class TestBaselineAndPassthrough:
    def test_baseline_keys_survive(self):
        result = _filtered(ANTHROPIC_API_KEY="sk-ant-x")
        for key in _BASELINE:
            assert result[key] == _BASELINE[key]
        assert "ANTHROPIC_API_KEY" not in result

    def test_passthrough_allowlist_wins_over_blocklist(self):
        env = dict(_BASELINE)
        env.update(
            GOOGLE_MAPS_API_KEY="maps-key",
            NAVER_CLIENT_SECRET="naver-secret",
            OPENAI_API_KEY="sk-x",
        )
        with patch.dict(os.environ, env, clear=True):
            result = filter_env(
                passthrough=["GOOGLE_MAPS_API_KEY", "NAVER_CLIENT_SECRET"]
            )

        assert result["GOOGLE_MAPS_API_KEY"] == "maps-key"
        assert result["NAVER_CLIENT_SECRET"] == "naver-secret"
        # allowlist에 없는 시크릿은 여전히 제거된다.
        assert "OPENAI_API_KEY" not in result

    def test_non_sensitive_vars_pass_by_default(self):
        result = _filtered(SIMPLECLAW_CONFIG="/tmp/config.yaml", TZ="Asia/Seoul")
        assert result["SIMPLECLAW_CONFIG"] == "/tmp/config.yaml"
        assert result["TZ"] == "Asia/Seoul"
