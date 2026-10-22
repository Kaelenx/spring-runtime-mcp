"""Official MCP SDK host over STDIO. Tool calls stay on the read-only path."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, TextIO

import mcp.types as types
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.shared.message import SessionMessage

from . import __version__
from .contracts.definitions import list_tools
from .models.source_catalog import SourceCatalog
from .models.common import CALL_BUDGET, CallBudget, NormalizationError
from .policy.sanitization import SanitizerPipeline
from .policy.configuration import ConfigReadPolicy
from .snapshots import SnapshotStore
from .services import Services
from .diagnostics import failed
from .tools.registry import HANDLERS, call_tool
from .tools.support import TOOL_TIMEOUT_SECONDS
from .transport.actuator_http import ActuatorHttpTransport
from .transport.response_gate import ENDPOINTS, ResponseGateReader
from .transport.responses import serialize_response, tool_result
from .transport.responses import MAX_RESPONSE_BYTES, _check_request_id

MAX_FRAME_BYTES = 64 * 1024
SERVER_NAME = "spring-runtime-mcp"
SERVER_VERSION = __version__
# The SDK stamps this on every 2026-era result; the budget measurement includes
# it so a response that fits our cap also fits the stamped wire frame.
SERVER_INFO_META: dict[str, Any] = {
    "io.modelcontextprotocol/serverInfo": {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
    }
}
_FORMAT_CHECKER = FormatChecker()


def _is_rfc3339(value: object) -> bool:
    if not isinstance(value, str):
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


_FORMAT_CHECKER.checkers["date-time"] = (_is_rfc3339, ())


def build_services(
    base_url: str,
    target_id: str = "local",
    timeout: float = 5.0,
    *,
    config_policy: ConfigReadPolicy | None = None,
    source_catalog: SourceCatalog | None = None,
    bearer_token: str | None = None,
) -> Services:
    """Assemble the real reader. No connection is opened until a tool calls it."""
    targets = frozenset({target_id})
    transport = ActuatorHttpTransport(
        base_url, targets=targets, bearer_token=bearer_token
    )
    reader = ResponseGateReader(
        transport,
        targets=targets,
        enabled_endpoints=ENDPOINTS - {"env", "configprops"}
        if config_policy is None
        else ENDPOINTS,
        timeout=timeout,
    )
    if source_catalog is None:
        # Missing workspace facts are recorded provenance, not absent code.
        catalog = SourceCatalog(target_id, None, {}, (), ("SOURCE_CATALOG_UNPROVIDED",))
    elif source_catalog.target_id != target_id:
        raise ValueError("SOURCE_TARGET_MISMATCH")
    else:
        catalog = source_catalog
    return Services(
        reader,
        catalog,
        SanitizerPipeline(
            known_secrets=() if bearer_token is None else (bearer_token,)
        ),
        SnapshotStore(),
        targets=targets,
        config_policy=config_policy,
    )


def _tool_models() -> list[types.Tool]:
    return [types.Tool.model_validate(item, by_alias=True) for item in list_tools()]


def _valid_envelope(schema: dict[str, Any], envelope: object) -> bool:
    if not isinstance(envelope, dict):
        return False
    try:
        Draft202012Validator(schema, format_checker=_FORMAT_CHECKER).validate(envelope)
    except ValidationError:
        return False
    return True


async def _call_tool(
    ctx: Any, params: types.CallToolRequestParams, services: Services
) -> types.CallToolResult:
    name = params.name
    catalog = {item["name"]: item for item in list_tools()}
    arguments = params.arguments or {}
    if name not in catalog:
        # Unknown names are protocol errors, not tool execution results.
        raise MCPError(-32602, "UNKNOWN_TOOL")
    try:
        Draft202012Validator(
            catalog[name]["inputSchema"], format_checker=_FORMAT_CHECKER
        ).validate(arguments)
    except ValidationError:
        return _result(failed("INVALID_ARGUMENT"), params_id(ctx))
    if name not in HANDLERS:
        return _result(failed("CAPABILITY_DISABLED"), params_id(ctx))
    try:
        envelope = await call_tool(name, arguments, services)
        if not _valid_envelope(catalog[name]["outputSchema"], envelope):
            source = envelope.get("source") if isinstance(envelope, dict) else None
            target = source.get("targetId") if isinstance(source, dict) else None
            envelope = failed(
                "OUTPUT_CONTRACT_VIOLATION",
                target if isinstance(target, str) else "local",
            )
    except Exception:
        envelope = failed("INTERNAL_ERROR")
    return _result(envelope, params_id(ctx))


def params_id(ctx: Any) -> str | int | None:
    request_id = getattr(ctx, "request_id", None)
    return request_id if isinstance(request_id, (str, int)) else None


def _result(
    envelope: dict[str, Any], request_id: str | int | None
) -> types.CallToolResult:
    fallback_id = request_id
    try:
        # Enforce the full-frame budget, server stamp included.
        serialize_response(envelope, request_id, server_meta=SERVER_INFO_META)
    except NormalizationError as exc:
        # Preserve the real rejection reason; an unsafe/sanitization failure is
        # not the same failure as an oversized frame. An invalid identity cannot
        # be echoed, so the safe fallback uses a null id.
        envelope = failed(exc.code)
        if exc.code == "INVALID_REQUEST_ID":
            fallback_id = None
        serialize_response(envelope, fallback_id, server_meta=SERVER_INFO_META)
    except Exception:
        envelope = failed("INTERNAL_ERROR")
        serialize_response(envelope, fallback_id, server_meta=SERVER_INFO_META)
    return types.CallToolResult.model_validate(tool_result(envelope), by_alias=True)


def build_server(services: Services) -> Server[Services]:
    async def list_tools_handler(
        ctx: Any, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        # Re-publish each committed contract entry unchanged, outputSchema included.
        return types.ListToolsResult(tools=_tool_models())

    async def call_tool_handler(
        ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        scope = CallBudget(time.monotonic() + TOOL_TIMEOUT_SECONDS)
        token = CALL_BUDGET.set(scope)
        try:
            async with asyncio.timeout(scope.check()):
                result = await _call_tool(ctx, params, services)
                scope.check()
                return result
        except (TimeoutError, NormalizationError):
            # Expired scopes cannot be used while serializing the safe fallback.
            CALL_BUDGET.reset(token)
            token = CALL_BUDGET.set(None)
            return _result(failed("TARGET_TIMEOUT"), params_id(ctx))
        finally:
            CALL_BUDGET.reset(token)

    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        on_list_tools=list_tools_handler,
        on_call_tool=call_tool_handler,
    )


class _BoundedLineReader:
    """UTF-8 stdin lines capped at the frame limit, one byte over so overflow is visible."""

    def __init__(self, binary: Any, limit: int = MAX_FRAME_BYTES) -> None:
        self._binary = binary
        self._limit = limit
        self.oversized = False
        self.invalid_identity = False

    def readline(self, size: int = -1) -> str:
        line = self._binary.readline(self._limit + 1)
        if len(line) > self._limit:
            self.oversized = True
            return ""
        decoded: str = line.decode("utf-8", errors="replace")
        try:
            message = json.loads(decoded)
            identifier = message.get("id") if isinstance(message, dict) else None
            if type(identifier) in (str, int):
                _check_request_id(identifier)
        except (ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, NormalizationError):
                self.invalid_identity = True
                return ""
        return decoded

    async def read_line(self) -> str:
        # The stdio transport awaits this; the bounded read stays off the loop.
        return await asyncio.to_thread(self.readline)

    def __aiter__(self) -> _BoundedLineReader:
        return self

    async def __anext__(self) -> str:
        line = await self.read_line()
        if not line:
            raise StopAsyncIteration
        return line


class _BoundedWriteStream:
    """Check SDK-serialized tool frames at the last send boundary."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aenter__(self) -> _BoundedWriteStream:
        await self._stream.__aenter__()
        return self

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def __aexit__(self, *args: Any) -> None:
        await self._stream.__aexit__(*args)

    async def send(self, item: SessionMessage) -> None:
        message = item.message
        if (
            isinstance(message, types.JSONRPCResponse)
            and "structuredContent" in message.result
        ):
            size = len(
                message.model_dump_json(by_alias=True, exclude_unset=True).encode(
                    "utf-8"
                )
            )
            if size > MAX_RESPONSE_BYTES:
                message = types.JSONRPCResponse(
                    jsonrpc="2.0",
                    id=message.id,
                    result=tool_result(
                        failed("RESPONSE_TOO_LARGE"), server_meta=SERVER_INFO_META
                    ),
                )
                _check_request_id(message.id)
                if (
                    len(
                        message.model_dump_json(
                            by_alias=True, exclude_unset=True
                        ).encode("utf-8")
                    )
                    > MAX_RESPONSE_BYTES
                ):
                    raise ValueError("RESPONSE_TOO_LARGE")
                item = replace(item, message=message)
        # The destination is the SDK's protocol stream, not a socket. Its stdio
        # writer retains ownership of fd 1 and all JSON-RPC framing.
        await self._stream.send(item)


async def run_stdio(services: Services, stdout: TextIO | None = None) -> None:
    """Serve MCP over the process's stdin/stdout until EOF.

    A frame over the limit is rejected once and ends the session, so its
    remaining bytes cannot be re-read as later requests.
    """
    reader = _BoundedLineReader(sys.stdin.buffer)
    server = build_server(services)
    async with stdio_server(stdin=reader) as (read_stream, write_stream):  # type: ignore[arg-type]
        await server.run(
            read_stream,
            _BoundedWriteStream(write_stream),
            server.create_initialization_options(),
        )
    if reader.oversized or reader.invalid_identity:
        frame = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "INVALID_REQUEST"},
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        channel = stdout if stdout is not None else sys.stdout
        channel.write(frame + "\n")
        channel.flush()
