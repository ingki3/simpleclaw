"""simpleclaw.config 모듈의 단위 테스트.

각 load_*_config() 함수가 YAML 설정 파일을 올바르게 파싱하고,
파일 누락·잘못된 YAML·비정상 데이터 타입 시 안전하게 기본값을 반환하는지 검증한다.

주요 테스트 시나리오:
- 설정 파일이 없을 때 각 로더가 해당 기본값(_*_DEFAULTS)을 반환하는지
- 유효한 YAML을 읽어 올바른 값으로 파싱하는지
- 잘못된 YAML 구문이나 비-dict 최상위 데이터에 대해 기본값으로 폴백하는지
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simpleclaw.config import (
    _AGENT_DEFAULTS,
    _DAEMON_DEFAULTS,
    _DEFAULTS,
    _LLM_DEFAULTS,
    _SUB_AGENTS_DEFAULTS,
    _TELEGRAM_DEFAULTS,
    _VOICE_DEFAULTS,
    _WEBHOOK_DEFAULTS,
    load_agent_config,
    load_daemon_config,
    load_llm_config,
    load_persona_config,
    load_sub_agents_config,
    load_telegram_config,
    load_voice_config,
    load_webhook_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 모든 로더 함수와 대응하는 기본값을 쌍으로 묶어 parametrize 테스트에 활용
ALL_LOADERS = [
    (load_persona_config, _DEFAULTS),
    (load_llm_config, _LLM_DEFAULTS),
    (load_agent_config, _AGENT_DEFAULTS),
    (load_daemon_config, _DAEMON_DEFAULTS),
    (load_voice_config, _VOICE_DEFAULTS),
    (load_telegram_config, _TELEGRAM_DEFAULTS),
    (load_webhook_config, _WEBHOOK_DEFAULTS),
    (load_sub_agents_config, _SUB_AGENTS_DEFAULTS),
]


def _write_yaml(path: Path, content: str) -> Path:
    """테스트용 YAML 파일을 임시 경로에 작성하는 헬퍼."""
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. load_persona_config
# ---------------------------------------------------------------------------


class TestLoadPersonaConfig:
    """persona 섹션의 설정 로딩을 검증한다 (token_budget, local_dir, files 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _DEFAULTS 기본값이 그대로 반환되어야 한다."""
        result = load_persona_config(tmp_path / "missing.yaml")
        assert result == _DEFAULTS

    def test_reads_valid_config(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
persona:
  token_budget: 8192
  local_dir: ".custom"
  global_dir: "~/.custom/global"
  files:
    - name: CUSTOM.md
      type: custom
""",
        )
        result = load_persona_config(cfg)
        # YAML에 명시한 값이 기본값을 오버라이드해야 함
        assert result["token_budget"] == 8192
        assert result["local_dir"] == ".custom"
        assert result["global_dir"] == "~/.custom/global"
        assert result["files"] == [{"name": "CUSTOM.md", "type": "custom"}]


# ---------------------------------------------------------------------------
# 2. load_llm_config
# ---------------------------------------------------------------------------


class TestLoadLlmConfig:
    """llm 섹션의 설정 로딩을 검증한다 (providers, default, api_key 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _LLM_DEFAULTS가 반환되어야 한다."""
        result = load_llm_config(tmp_path / "missing.yaml")
        assert result == _LLM_DEFAULTS

    def test_reads_providers_with_api_key(self, tmp_path: Path):
        """provider에 api_key가 명시되면 해당 값이 그대로 로드되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
llm:
  default: openai
  providers:
    openai:
      model: gpt-4
      api_key: sk-test-key
""",
        )
        result = load_llm_config(cfg)
        assert result["default"] == "openai"
        assert "openai" in result["providers"]
        p = result["providers"]["openai"]
        assert p["api_key"] == "sk-test-key"
        assert p["model"] == "gpt-4"
        # provider 이름은 딕셔너리 키에서 자동 주입됨
        assert p["name"] == "openai"

    def test_missing_api_key_defaults_to_empty(self, tmp_path: Path):
        """api_key를 생략하면 빈 문자열로 기본 설정되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
llm:
  providers:
    claude:
      model: claude-3
""",
        )
        result = load_llm_config(cfg)
        assert result["providers"]["claude"]["api_key"] == ""

    def test_non_dict_provider_is_skipped(self, tmp_path: Path):
        """provider 값이 dict가 아닌 경우(문자열 등) 해당 provider는 무시되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
llm:
  providers:
    bad_provider: "just a string"
    good_provider:
      model: gpt-4
      api_key: key123
""",
        )
        result = load_llm_config(cfg)
        # 문자열 값인 bad_provider는 건너뛰고 dict인 good_provider만 로드됨
        assert "bad_provider" not in result["providers"]
        assert "good_provider" in result["providers"]


# ---------------------------------------------------------------------------
# 3. load_agent_config
# ---------------------------------------------------------------------------


class TestLoadAgentConfig:
    """agent 섹션의 설정 로딩을 검증한다 (history_limit, db_path 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _AGENT_DEFAULTS가 반환되어야 한다."""
        result = load_agent_config(tmp_path / "missing.yaml")
        assert result == _AGENT_DEFAULTS

    def test_reads_valid_config(self, tmp_path: Path):
        """유효한 agent 설정이 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
agent:
  history_limit: 50
  db_path: "custom.db"
  max_tool_iterations: 10
  workspace_dir: "/tmp/ws"
""",
        )
        result = load_agent_config(cfg)
        assert result["history_limit"] == 50
        assert result["db_path"] == "custom.db"
        assert result["max_tool_iterations"] == 10
        assert result["workspace_dir"] == "/tmp/ws"


# ---------------------------------------------------------------------------
# 4. load_daemon_config
# ---------------------------------------------------------------------------


class TestLoadDaemonConfig:
    """daemon 섹션의 설정 로딩을 검증한다 (heartbeat, dreaming, wait_state 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _DAEMON_DEFAULTS가 반환되어야 한다."""
        result = load_daemon_config(tmp_path / "missing.yaml")
        assert result == _DAEMON_DEFAULTS

    def test_reads_valid_config(self, tmp_path: Path):
        """중첩 구조(dreaming, wait_state)를 포함한 daemon 설정이 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
daemon:
  heartbeat_interval: 60
  pid_file: "/run/daemon.pid"
  dreaming:
    overnight_hour: 5
    idle_threshold: 3600
    model: "gpt-4"
  wait_state:
    default_timeout: 7200
""",
        )
        result = load_daemon_config(cfg)
        assert result["heartbeat_interval"] == 60
        assert result["pid_file"] == "/run/daemon.pid"
        assert result["dreaming"]["overnight_hour"] == 5
        assert result["dreaming"]["model"] == "gpt-4"
        assert result["wait_state"]["default_timeout"] == 7200


# ---------------------------------------------------------------------------
# 5. load_voice_config
# ---------------------------------------------------------------------------


class TestLoadVoiceConfig:
    """voice 섹션의 설정 로딩을 검증한다 (stt/tts provider, model, voice 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _VOICE_DEFAULTS가 반환되어야 한다."""
        result = load_voice_config(tmp_path / "missing.yaml")
        assert result == _VOICE_DEFAULTS

    def test_reads_valid_config(self, tmp_path: Path):
        """stt/tts 하위 설정이 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
voice:
  stt:
    provider: google
    model: chirp
    max_duration: 600
  tts:
    provider: google
    model: wavenet
    voice: nova
    speed: 1.5
    output_format: wav
    max_text_length: 8192
""",
        )
        result = load_voice_config(cfg)
        assert result["stt"]["provider"] == "google"
        assert result["stt"]["max_duration"] == 600
        assert result["tts"]["voice"] == "nova"
        assert result["tts"]["speed"] == 1.5
        assert result["tts"]["output_format"] == "wav"


# ---------------------------------------------------------------------------
# 6. load_telegram_config
# ---------------------------------------------------------------------------


class TestLoadTelegramConfig:
    """telegram 섹션의 설정 로딩을 검증한다 (bot_token, whitelist 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _TELEGRAM_DEFAULTS가 반환되어야 한다."""
        result = load_telegram_config(tmp_path / "missing.yaml")
        assert result == _TELEGRAM_DEFAULTS

    def test_reads_bot_token_from_yaml(self, tmp_path: Path):
        """bot_token과 whitelist(user_ids, chat_ids)가 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
telegram:
  bot_token: "123456:ABC-DEF"
  whitelist:
    user_ids:
      - 111
      - 222
    chat_ids:
      - -100333
""",
        )
        result = load_telegram_config(cfg)
        assert result["bot_token"] == "123456:ABC-DEF"
        assert result["whitelist"]["user_ids"] == [111, 222]
        assert result["whitelist"]["chat_ids"] == [-100333]


# ---------------------------------------------------------------------------
# 7. load_webhook_config
# ---------------------------------------------------------------------------


class TestLoadWebhookConfig:
    """webhook 섹션의 설정 로딩을 검증한다 (enabled, host, port, auth_token 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _WEBHOOK_DEFAULTS가 반환되어야 한다."""
        result = load_webhook_config(tmp_path / "missing.yaml")
        assert result == _WEBHOOK_DEFAULTS

    def test_reads_auth_token_from_yaml(self, tmp_path: Path):
        """webhook의 enabled, host, port, auth_token이 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
webhook:
  enabled: false
  host: "0.0.0.0"
  port: 9090
  auth_token: "secret-token-123"
""",
        )
        result = load_webhook_config(cfg)
        assert result["enabled"] is False
        assert result["host"] == "0.0.0.0"
        assert result["port"] == 9090
        assert result["auth_token"] == "secret-token-123"


# ---------------------------------------------------------------------------
# 8. load_sub_agents_config
# ---------------------------------------------------------------------------


class TestLoadSubAgentsConfig:
    """sub_agents 섹션의 설정 로딩을 검증한다 (max_concurrent, default_scope 등)."""

    def test_defaults_when_file_missing(self, tmp_path: Path):
        """설정 파일이 존재하지 않으면 _SUB_AGENTS_DEFAULTS가 반환되어야 한다."""
        result = load_sub_agents_config(tmp_path / "missing.yaml")
        assert result == _SUB_AGENTS_DEFAULTS

    def test_reads_valid_config(self, tmp_path: Path):
        """중첩 구조(default_scope)를 포함한 sub_agents 설정이 올바르게 파싱되어야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(
            cfg,
            """\
sub_agents:
  max_concurrent: 5
  default_timeout: 600
  workspace_dir: "/tmp/sub"
  cleanup_workspace: true
  default_scope:
    allowed_paths:
      - /home/user/project
    network: true
""",
        )
        result = load_sub_agents_config(cfg)
        assert result["max_concurrent"] == 5
        assert result["default_timeout"] == 600
        assert result["cleanup_workspace"] is True
        assert result["default_scope"]["allowed_paths"] == ["/home/user/project"]
        assert result["default_scope"]["network"] is True


# ---------------------------------------------------------------------------
# 9. All functions handle invalid YAML gracefully
# ---------------------------------------------------------------------------


class TestInvalidYaml:
    """잘못된 YAML 구문에 대한 방어 처리를 검증한다."""

    @pytest.mark.parametrize("loader,defaults", ALL_LOADERS)
    def test_invalid_yaml_returns_defaults(self, tmp_path: Path, loader, defaults):
        """파싱 불가능한 YAML이면 예외 없이 기본값을 반환해야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(cfg, "{{{{invalid yaml: [[[")
        result = loader(cfg)
        assert result == defaults


# ---------------------------------------------------------------------------
# 10. All functions handle non-dict data gracefully
# ---------------------------------------------------------------------------


class TestNonDictData:
    """최상위 데이터가 dict가 아닌 경우(리스트, 스칼라)의 방어 처리를 검증한다."""

    @pytest.mark.parametrize("loader,defaults", ALL_LOADERS)
    def test_non_dict_top_level_returns_defaults(
        self, tmp_path: Path, loader, defaults
    ):
        """YAML 최상위가 리스트이면 기본값을 반환해야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(cfg, "- just\n- a\n- list\n")
        result = loader(cfg)
        assert result == defaults

    @pytest.mark.parametrize("loader,defaults", ALL_LOADERS)
    def test_scalar_yaml_returns_defaults(self, tmp_path: Path, loader, defaults):
        """YAML 최상위가 스칼라 값(숫자 등)이면 기본값을 반환해야 한다."""
        cfg = tmp_path / "config.yaml"
        _write_yaml(cfg, "42\n")
        result = loader(cfg)
        assert result == defaults
