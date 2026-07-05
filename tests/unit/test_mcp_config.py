"""MCP config loader tests."""

from pathlib import Path

from simpleclaw.config import load_mcp_config


def test_mcp_config_defaults_when_missing(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("llm: {}\n", encoding="utf-8")

    cfg = load_mcp_config(path)

    assert cfg == {"enabled": False, "servers": {}}


def test_mcp_config_defaults_when_file_missing(tmp_path: Path):
    cfg = load_mcp_config(tmp_path / "missing.yaml")

    assert cfg == {"enabled": False, "servers": {}}


def test_mcp_config_loads_stdio_server(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
mcp:
  enabled: true
  servers:
    time:
      transport: stdio
      command: uvx
      args: [mcp-server-time]
      timeout: 30
      connect_timeout: 20
      scope: runtime
      env:
        TZ: Asia/Seoul
""".strip(),
        encoding="utf-8",
    )

    cfg = load_mcp_config(path)

    assert cfg["enabled"] is True
    assert cfg["servers"]["time"]["transport"] == "stdio"
    assert cfg["servers"]["time"]["command"] == "uvx"
    assert cfg["servers"]["time"]["args"] == ["mcp-server-time"]
    assert cfg["servers"]["time"]["timeout"] == 30
    assert cfg["servers"]["time"]["connect_timeout"] == 20
    assert cfg["servers"]["time"]["scope"] == "runtime"
    assert cfg["servers"]["time"]["env"] == {"TZ": "Asia/Seoul"}


def test_invalid_server_shapes_are_dropped(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
mcp:
  enabled: true
  servers:
    bad-list: []
    missing-command:
      transport: stdio
    http-not-supported:
      transport: http
      command: some-server
    valid:
      command: python
""".strip(),
        encoding="utf-8",
    )

    cfg = load_mcp_config(path)

    assert sorted(cfg["servers"]) == ["valid"]
    assert cfg["servers"]["valid"]["transport"] == "stdio"
    assert cfg["servers"]["valid"]["scope"] == "operator"


def test_invalid_timeout_and_scope_fall_back_to_defaults(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
mcp:
  servers:
    weird:
      command: python
      timeout: -5
      connect_timeout: abc
      scope: superuser
""".strip(),
        encoding="utf-8",
    )

    cfg = load_mcp_config(path)

    # 서버가 있고 enabled 미지정 → enabled=True로 간주
    assert cfg["enabled"] is True
    assert cfg["servers"]["weird"]["timeout"] == 120
    assert cfg["servers"]["weird"]["connect_timeout"] == 30
    assert cfg["servers"]["weird"]["scope"] == "operator"
