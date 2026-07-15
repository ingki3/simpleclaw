"""MCP (Model Context Protocol) 클라이언트 관리자.

외부 MCP 서버에 연결하여 도구(tool)를 검색하고 호출하는 기능을 제공한다.

동작 흐름:
1. 설정에 정의된 MCP 서버들에 stdio 방식으로 연결
2. 각 서버에서 제공하는 도구 목록을 로드하여 내부 레지스트리에 등록
3. 에이전트 요청 시 해당 도구를 찾아 MCP 서버에 호출을 위임

설계 결정:
- 서버 연결 실패 시 해당 서버만 건너뛰고 나머지는 계속 연결 (부분 장애 허용).
  실패 원인은 서버별로 보존해 asset_inventory가 진단에 사용한다.
- 도구 호출마다 새 세션을 생성하므로 장기 연결 유지 부담 없음
- 도구 레지스트리는 ``(server, tool)`` 튜플로 키를 잡아 서로 다른 서버가 같은
  이름의 도구를 제공해도 충돌하지 않는다.
- MCP 서버는 외부 코드이므로 subprocess에 전체 환경을 상속하지 않는다.
  baseline 키 + 설정에 명시된 env만 전달하고, env 값의 secret reference는
  주입된 resolver로 실행 직전에 해석한다.
- mcp 패키지 미설치 시 ImportError를 MCPConnectionError로 변환하여 명확한 에러 메시지 제공
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from simpleclaw.skills.models import (
    MCPConnectionError,
    ToolDefinition,
    ToolSource,
)

logger = logging.getLogger(__name__)

# stdio subprocess에 기본으로 전달하는 환경변수 키. 실행에 필요한 최소 집합만
# 남기고 API 키 등 시크릿성 변수의 암묵 상속을 차단한다.
_BASELINE_ENV_KEYS = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"}

# env 값의 secret reference("file:name" 등)를 실제 값으로 바꾸는 hook.
# None을 반환하면 해당 키는 전달하지 않는다.
SecretResolver = Callable[[str], str | None]

# BIZ-443: 인라인 코드 실행 인터프리터 — `-c`/`-e` 조합이면 argv 감사가 무의미해짐
_INLINE_EXEC_COMMANDS = {"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "node"}
_INLINE_EXEC_FLAGS = {"-c", "-e", "--eval"}


def _validate_stdio_config(name: str, config: dict) -> None:
    """의심스러운 stdio 서버 설정을 fail-closed로 차단한다.

    MCP 서버 command는 운영자 설정이지만, `sh -c "..."` / `python -c "..."` 처럼
    인라인 코드 문자열을 실행하는 형태는 argv 수준의 감사·재현이 불가능하고
    설정 주입 한 줄로 임의 셸 실행이 되는 통로다. 정상 MCP 서버는 실행 파일 +
    인자 형태로 기동 가능하므로 인라인 실행 조합은 명확한 오류로 거부한다
    (해당 서버만 건너뛰고 나머지 서버는 계속 연결된다 — BIZ-443).

    Raises:
        MCPConnectionError: 인라인 실행 조합이 감지된 경우.
    """
    command = str(config.get("command") or "")
    base = os.path.basename(command).lower()
    if base not in _INLINE_EXEC_COMMANDS:
        return
    args = [str(a) for a in (config.get("args") or [])]
    flagged = sorted(_INLINE_EXEC_FLAGS.intersection(args))
    if flagged:
        raise MCPConnectionError(
            f"MCP server '{name}' config blocked: inline-exec invocation "
            f"('{base}' with {', '.join(flagged)}) is not allowed. "
            "Point 'command' at the server executable (or use 'python -m <module>') "
            "instead of an inline code string."
        )


class MCPManager:
    """MCP 서버 연결을 관리하고 도구 접근을 제공하는 클래스."""

    def __init__(self, *, secret_resolver: SecretResolver | None = None) -> None:
        # (서버 이름, 도구 이름) -> 도구 정의 — 서버 간 이름 충돌 방지
        self._tools: dict[tuple[str, str], ToolDefinition] = {}
        self._connected_servers: list[str] = []  # 연결 성공한 서버 이름 목록
        self._server_configs: dict[str, dict] = {}  # 서버 이름 -> 설정 (재연결 시 사용)
        self._connection_errors: dict[str, str] = {}  # 서버 이름 -> 연결 실패 요약
        # resolver 미주입 시 값을 그대로 통과 — manager를 security 모듈과 결합하지
        # 않고 테스트 가능하게 유지한다.
        self._secret_resolver: SecretResolver = secret_resolver or (lambda value: value)

    async def connect_servers(self, mcp_config: dict) -> None:
        """설정에 정의된 MCP 서버들에 연결한다.

        개별 서버 연결 실패 시 로그와 오류 요약을 남기고 건너뛴다 — 에이전트는
        해당 서버의 도구 없이 계속 동작한다.

        Args:
            mcp_config: MCP 설정 딕셔너리 (servers 키 포함)
        """
        servers = mcp_config.get("servers", {})
        if not servers:
            logger.debug("No MCP servers configured.")
            return

        for name, server_conf in servers.items():
            try:
                # 응답 없는 서버가 부팅/turn 준비를 무한정 잡지 않도록 연결 전체에
                # connect_timeout을 건다.
                connect_timeout = _positive_number(
                    server_conf.get("connect_timeout"), 30
                )
                await asyncio.wait_for(
                    self._connect_server(name, server_conf),
                    timeout=connect_timeout,
                )
                self._connection_errors.pop(name, None)
            except Exception as e:
                self._connection_errors[name] = str(e)[:500]
                logger.warning(
                    "Failed to connect to MCP server '%s': %s. "
                    "Agent will continue without this server's tools.",
                    name, e,
                )

    async def _connect_server(self, name: str, config: dict) -> None:
        """단일 MCP 서버에 연결하고 제공하는 도구들을 로드한다."""
        command = config.get("command")

        if not command:
            raise MCPConnectionError(
                f"MCP server '{name}' has no command specified"
            )

        # BIZ-443: 인라인 셸/코드 실행형 설정은 연결 전에 명확한 오류로 차단.
        _validate_stdio_config(name, config)

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=command,
                args=config.get("args", []),
                env=self._build_subprocess_env(config.get("env")),
            )

            loaded_tools = 0
            scope = str(config.get("scope") or "operator")

            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    try:
                        initialize_result = await session.initialize()
                    except Exception as e:
                        raise MCPConnectionError(
                            f"MCP server '{name}' initialize failed: {e}"
                        ) from e

                    if not self._has_tools_capability(initialize_result):
                        logger.info(
                            "Connected to MCP server '%s' without tools/list "
                            "capability; loaded 0 tools.",
                            name,
                        )
                    else:
                        try:
                            tools_result = await session.list_tools()
                        except Exception as e:
                            raise MCPConnectionError(
                                f"MCP server '{name}' tools/list failed: {e}"
                            ) from e

                        for tool in tools_result.tools:
                            tool_def = ToolDefinition(
                                name=tool.name,
                                description=tool.description or "",
                                source=ToolSource.MCP,
                                source_name=name,
                                metadata={
                                    "input_schema": _extract_input_schema(tool),
                                    "scope": scope,
                                },
                            )
                            self._tools[(name, tool.name)] = tool_def
                        loaded_tools = len(tools_result.tools)

            if name not in self._connected_servers:
                self._connected_servers.append(name)
            self._server_configs[name] = config
            logger.info(
                "Connected to MCP server '%s', loaded %d tools.",
                name, loaded_tools,
            )

        except ImportError:
            raise MCPConnectionError(
                "MCP package not installed. Install with: pip install mcp"
            )
        except MCPConnectionError:
            raise
        except Exception as e:
            raise MCPConnectionError(
                f"Failed to connect to MCP server '{name}': {e}"
            ) from e

    def _build_subprocess_env(
        self, configured_env: dict[str, str] | None
    ) -> dict[str, str]:
        """MCP subprocess에 전달할 환경변수를 조립한다.

        baseline 키(PATH/HOME 등)와 XDG_* 만 상속하고, 설정 ``env:``에 명시된
        키만 secret resolver를 거쳐 추가한다 — 봇 프로세스가 들고 있는 다른
        시크릿이 외부 MCP 서버로 새지 않도록 하는 보안 경계다.
        """
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            if key in _BASELINE_ENV_KEYS or key.startswith("XDG_"):
                env[key] = value

        for key, value in (configured_env or {}).items():
            resolved = self._secret_resolver(str(value))
            if resolved is not None:
                env[str(key)] = str(resolved)
        return env

    @staticmethod
    def _has_tools_capability(initialize_result: object) -> bool:
        """initialize 응답에 tools/list capability가 선언됐는지 확인한다."""
        capabilities = getattr(initialize_result, "capabilities", None)
        if capabilities is None and isinstance(initialize_result, dict):
            capabilities = initialize_result.get("capabilities")

        if capabilities is None:
            return False

        if isinstance(capabilities, dict):
            return capabilities.get("tools") is not None

        return getattr(capabilities, "tools", None) is not None

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict | None = None,
        *,
        scope: str = "runtime",
    ) -> str:
        """지정한 서버의 MCP 도구를 실행하고 결과를 반환한다.

        Args:
            server_name: 도구를 제공하는 설정된 MCP 서버 이름
            tool_name: 실행할 도구의 이름
            arguments: 도구에 전달할 인자 딕셔너리
            scope: 호출 컨텍스트 scope. operator-only 도구는 ``scope="operator"``
                에서만 호출을 허용한다 — runtime 사용자가 외부 코드 실행 도구를
                우회 호출하지 못하게 하는 경계다.

        Returns:
            도구 실행 결과 텍스트

        Raises:
            MCPConnectionError: 도구를 찾을 수 없거나 scope가 부족하거나 실행에 실패한 경우
        """
        tool = self._tools.get((server_name, tool_name))
        if tool is None:
            raise MCPConnectionError(
                f"MCP tool '{server_name}.{tool_name}' not found"
            )

        tool_scope = str(tool.metadata.get("scope") or "operator")
        if tool_scope == "operator" and scope != "operator":
            raise MCPConnectionError(
                f"MCP tool '{server_name}.{tool_name}' is operator-only"
            )

        server_conf = self._server_configs.get(server_name)
        if server_conf is None:
            raise MCPConnectionError(
                f"MCP server '{server_name}' config not found"
            )

        call_timeout = _positive_number(server_conf.get("timeout"), 120)
        try:
            return await asyncio.wait_for(
                self._call_tool_once(server_name, tool_name, server_conf, arguments),
                timeout=call_timeout,
            )
        except MCPConnectionError:
            raise
        except asyncio.TimeoutError as e:
            raise MCPConnectionError(
                f"MCP tool '{server_name}.{tool_name}' timed out after {call_timeout}s"
            ) from e
        except Exception as e:
            raise MCPConnectionError(
                f"Failed to call MCP tool '{server_name}.{tool_name}': {e}"
            ) from e

    async def _call_tool_once(
        self,
        server_name: str,
        tool_name: str,
        server_conf: dict,
        arguments: dict | None,
    ) -> str:
        """새 stdio 세션을 열어 도구를 1회 호출한다."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            raise MCPConnectionError(
                "MCP package not installed. Install with: pip install mcp"
            )

        server_params = StdioServerParameters(
            command=server_conf["command"],
            args=server_conf.get("args", []),
            env=self._build_subprocess_env(server_conf.get("env")),
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool_name, arguments=arguments or {}
                )
                texts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        texts.append(item.text)
                return "\n".join(texts) if texts else str(result)

    def list_tools(self) -> list[ToolDefinition]:
        """연결된 모든 MCP 서버의 도구 목록을 반환한다."""
        return list(self._tools.values())

    def get_connected_servers(self) -> list[str]:
        """연결에 성공한 서버들의 이름 목록을 반환한다."""
        return list(self._connected_servers)

    def get_connection_errors(self) -> dict[str, str]:
        """연결 실패 서버별 오류 요약을 반환한다."""
        return dict(self._connection_errors)


def _extract_input_schema(tool: object) -> dict:
    """MCP SDK tool 객체에서 input schema를 방어적으로 추출한다."""
    input_schema = getattr(tool, "inputSchema", None)
    if input_schema is None:
        input_schema = getattr(tool, "input_schema", None)
    return input_schema if isinstance(input_schema, dict) else {}


def _positive_number(value: object, default: float) -> float:
    """양수만 허용하고 그 외는 default로 폴백한다."""
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
