"""MCP asset inventory tests.

``asset_inventory(type="mcp")``가 configured/connected/tools/scope/schema/
connection error를 안전하게 요약하고, 시크릿성 env 값은 노출하지 않는지 검증한다.
"""

import json
from types import SimpleNamespace

from simpleclaw.agent.asset_inventory import handle_asset_inventory


class FakeManager:
    def get_connected_servers(self):
        return ["time"]

    def list_tools(self):
        return [
            SimpleNamespace(
                name="now",
                source_name="time",
                metadata={
                    "scope": "runtime",
                    "input_schema": {"type": "object", "properties": {}},
                },
            )
        ]

    def get_connection_errors(self):
        return {"bad": "command not found"}


def _write_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
mcp:
  enabled: true
  servers:
    time:
      command: uvx
      args: [mcp-server-time]
      env:
        API_TOKEN: "file:mcp_api_token"
    bad:
      command: bad-cmd
""".strip(),
        encoding="utf-8",
    )
    return config


def test_mcp_inventory_includes_scope_schema_and_errors(tmp_path):
    config = _write_config(tmp_path)

    result = handle_asset_inventory(
        {"type": "mcp", "include_paths": True},
        config_path=config,
        mcp_manager=FakeManager(),
    )
    payload = json.loads(result)
    section = payload["sections"]["mcp"]

    assert section["configured_servers"] == ["bad", "time"]
    assert section["connected_servers"] == ["time"]
    assert section["connection_errors"] == {"bad": "command not found"}
    assert section["tools"] == [
        {
            "name": "now",
            "source_name": "time",
            "scope": "runtime",
            "has_input_schema": True,
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_mcp_inventory_hides_env_values_and_schema_without_include_paths(tmp_path):
    config = _write_config(tmp_path)

    result = handle_asset_inventory(
        {"type": "mcp", "include_paths": True},
        config_path=config,
        mcp_manager=FakeManager(),
    )

    # env는 키만 노출 — secret reference 문자열조차 응답에 실리지 않아야 한다.
    payload = json.loads(result)
    server_configs = payload["sections"]["mcp"]["server_configs"]
    assert server_configs["time"]["env_keys"] == ["API_TOKEN"]
    assert "file:mcp_api_token" not in result

    # include_paths=False면 전체 input_schema는 생략되고 has_input_schema만 남는다.
    compact = json.loads(
        handle_asset_inventory(
            {"type": "mcp"},
            config_path=config,
            mcp_manager=FakeManager(),
        )
    )
    tool = compact["sections"]["mcp"]["tools"][0]
    assert tool["has_input_schema"] is True
    assert "input_schema" not in tool


def test_mcp_inventory_without_manager_reports_config_only(tmp_path):
    config = _write_config(tmp_path)

    payload = json.loads(
        handle_asset_inventory({"type": "mcp"}, config_path=config, mcp_manager=None)
    )
    section = payload["sections"]["mcp"]

    assert section["configured_servers"] == ["bad", "time"]
    assert section["connected_servers"] == []
    assert section["connection_errors"] == {}
    assert section["tools"] == []
