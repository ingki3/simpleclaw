"""Tests for subprocess environment variable filtering."""

import os
from unittest.mock import patch

from simpleclaw.security.env_filter import DEFAULT_BLOCKLIST, filter_env


class TestEnvFilter:
    """Tests for filter_env()."""

    def _make_env(self, **kwargs):
        """Create a controlled environment dict for testing."""
        base = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/user",
            "LANG": "en_US.UTF-8",
            "USER": "testuser",
        }
        base.update(kwargs)
        return base

    def test_removes_api_keys(self):
        env = self._make_env(
            OPENAI_API_KEY="sk-xxx",
            ANTHROPIC_API_KEY="sk-ant-xxx",
            GOOGLE_API_KEY="AIza-xxx",
            CUSTOM_API_KEY="custom-xxx",
        )
        with patch.dict(os.environ, env, clear=True):
            result = filter_env()

        assert "OPENAI_API_KEY" not in result
        assert "ANTHROPIC_API_KEY" not in result
        assert "GOOGLE_API_KEY" not in result
        assert "CUSTOM_API_KEY" not in result

    def test_removes_tokens(self):
        env = self._make_env(
            TELEGRAM_BOT_TOKEN="123:abc",
            GH_TOKEN="ghp_xxx",
            WEBHOOK_AUTH_TOKEN="secret",
        )
        with patch.dict(os.environ, env, clear=True):
            result = filter_env()

        assert "TELEGRAM_BOT_TOKEN" not in result
        assert "GH_TOKEN" not in result
        assert "WEBHOOK_AUTH_TOKEN" not in result

    def test_removes_provider_prefixed_keys(self):
        env = self._make_env(
            OPENAI_ORG_ID="org-xxx",
            ANTHROPIC_BASE_URL="https://...",
            GOOGLE_APPLICATION_CREDENTIALS="/path",
            AWS_SECRET_ACCESS_KEY="xxx",
            TELEGRAM_CHAT_ID="12345",
            GITHUB_APP_ID="123",
        )
        with patch.dict(os.environ, env, clear=True):
            result = filter_env()

        for key in ["OPENAI_ORG_ID", "ANTHROPIC_BASE_URL",
                     "GOOGLE_APPLICATION_CREDENTIALS", "AWS_SECRET_ACCESS_KEY",
                     "TELEGRAM_CHAT_ID", "GITHUB_APP_ID"]:
            assert key not in result

    def test_preserves_safe_keys(self):
        env = self._make_env()
        with patch.dict(os.environ, env, clear=True):
            result = filter_env()

        assert result["PATH"] == "/usr/bin:/bin"
        assert result["HOME"] == "/home/user"
        assert result["LANG"] == "en_US.UTF-8"
        assert result["USER"] == "testuser"

    def test_passthrough_rescues_blocked_key(self):
        env = self._make_env(GOOGLE_API_KEY="AIza-xxx")
        with patch.dict(os.environ, env, clear=True):
            result = filter_env(passthrough=["GOOGLE_API_KEY"])

        assert result["GOOGLE_API_KEY"] == "AIza-xxx"

    def test_custom_blocklist(self):
        env = self._make_env(
            MY_CUSTOM_SECRET="xxx",
            OPENAI_API_KEY="sk-xxx",
        )
        with patch.dict(os.environ, env, clear=True):
            result = filter_env(blocklist=["MY_CUSTOM_*"])

        # Custom blocklist replaces default, so OPENAI_API_KEY stays
        assert "MY_CUSTOM_SECRET" not in result
        assert "OPENAI_API_KEY" in result

    def test_empty_env(self):
        with patch.dict(os.environ, {}, clear=True):
            result = filter_env()
        assert result == {}

    def test_default_blocklist_has_expected_patterns(self):
        assert "*_API_KEY" in DEFAULT_BLOCKLIST
        assert "*_TOKEN" in DEFAULT_BLOCKLIST
        assert "*_SECRET" in DEFAULT_BLOCKLIST
        assert "TELEGRAM_*" in DEFAULT_BLOCKLIST
        assert "OPENAI_*" in DEFAULT_BLOCKLIST
        assert "ANTHROPIC_*" in DEFAULT_BLOCKLIST
