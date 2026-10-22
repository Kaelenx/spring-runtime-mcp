"""Minimal MCP stdio host. Unimplemented tools return a failed envelope."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .contracts.definitions import list_tools
from .models.source_catalog import SourceCatalog
from .policy.sanitization import SanitizerPipeline
from .policy.configuration import ConfigReadPolicy
from .snapshots import SnapshotStore
from .tools.inspect_beans import Services, failed
from .tools.registry import HANDLERS, call_tool
from .transport.actuator_http import ActuatorHttpTransport
from .transport.response_gate import ENDPOINTS, ResponseGateReader
from .transport.responses import serialize_response
from .transport.stdio import quiet_console

SERVER_VERSION = "0.1.0"
MAX_FRAME_BYTES = 64 * 1024
MAX_REQUEST_ID_BYTES = 128
# Legacy-era versions this self-implemented host actually serves. 2026-07-28 is
# a modern-era revision (per-request `_meta`, server/discover) and must not be
# claimed until the official SDK or an equivalent implementation lands.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18",)


def build_services(
    base_url: str,
    target_id: str = "local",
    timeout: float = 5.0,
    *,
    config_policy: ConfigReadPolicy | None = None,
    source_catalog: SourceCatalog | None = None,
) -> Services:
    """Assemble the real reader. No connection is opened until a tool calls it."""
    targets = frozenset({target_id})
    transport = ActuatorHttpTransport(base_url, targets=targets)
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
        SanitizerPipeline(),
        SnapshotStore(),
        targets=targets,
        config_policy=config_policy,
    )


def _encode(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _error(message_id: object, code: int, message: str) -> bytes:
    safe_id: object = None
    if message_id is None or isinstance(message_id, str):
        if not isinstance(message_id, str) or len(message_id.encode("utf-8")) <= 128:
            safe_id = message_id
    elif isinstance(message_id, int) and not isinstance(message_id, bool):
        safe_id = message_id
    return _encode(
        {
            "jsonrpc": "2.0",
            "id": safe_id,
            "error": {"code": code, "message": message},
        }
    )


def _tool_frame(message_id: object, envelope: dict[str, Any]) -> bytes:
    try:
        if isinstance(message_id, bool) or not isinstance(
            message_id, (str, int, type(None))
        ):
            return _error(None, -32600, "INVALID_REQUEST")
        return serialize_response(envelope, message_id) + b"\n"
    except Exception:
        return _error(None, -32603, "INTERNAL_ERROR")


def _descriptors() -> list[dict[str, Any]]:
    listed = []
    for item in list_tools():
        listed.append(
            {
                "name": item["name"],
                "description": item["description"],
                "inputSchema": item["inputSchema"],
                "outputSchema": item["outputSchema"],
                "annotations": item["annotations"],
            }
        )
    return listed


def _valid_request_id(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.encode("utf-8")) <= MAX_REQUEST_ID_BYTES
    return type(value) is int


def _valid_envelope(schema: dict[str, Any], envelope: object) -> bool:
    if not isinstance(envelope, dict):
        return False
    try:
        Draft202012Validator(schema).validate(envelope)
    except ValidationError:
        return False
    return True


async def _call_tool(message_id: object, params: object, services: Services) -> bytes:
    if not isinstance(params, dict):
        return _error(message_id, -32602, "INVALID_PARAMS")
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str):
        return _tool_frame(message_id, failed("INVALID_ARGUMENT"))
    catalog = {item["name"]: item for item in list_tools()}
    if name not in catalog:
        return _error(message_id, -32602, "UNKNOWN_TOOL")
    if not isinstance(arguments, dict):
        return _tool_frame(message_id, failed("INVALID_ARGUMENT"))
    try:
        Draft202012Validator(catalog[name]["inputSchema"]).validate(arguments)
    except ValidationError:
        return _tool_frame(message_id, failed("INVALID_ARGUMENT"))
    if name not in HANDLERS:
        return _tool_frame(message_id, failed("CAPABILITY_DISABLED"))
    envelope = await call_tool(name, arguments, services)
    if not _valid_envelope(catalog[name]["outputSchema"], envelope):
        source = envelope.get("source") if isinstance(envelope, dict) else None
        target = source.get("targetId") if isinstance(source, dict) else None
        return _tool_frame(
            message_id,
            failed(
                "OUTPUT_CONTRACT_VIOLATION",
                target if isinstance(target, str) else "local",
            ),
        )
    return _tool_frame(message_id, envelope)


async def dispatch(message: object, services: Services) -> bytes | None:
    """Return one JSON-RPC frame, or None for a notification."""
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        if isinstance(message, dict) and "id" in message:
            return _error(message.get("id"), -32600, "INVALID_REQUEST")
        return _error(None, -32600, "INVALID_REQUEST")
    if message.get("jsonrpc") != "2.0":
        return _error(message.get("id"), -32600, "INVALID_REQUEST")
    if "id" in message and not _valid_request_id(message["id"]):
        return _error(None, -32600, "INVALID_REQUEST")
    method = message["method"]
    has_id = "id" in message
    message_id = message.get("id")
    params = message.get("params", {})
    if not has_id:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else SUPPORTED_PROTOCOL_VERSIONS[0]
        )
        return _encode(
            {
                "jsonrpc": "2.0",
                "id": message_id if not isinstance(message_id, bool) else None,
                "result": {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "spring-runtime-mcp",
                        "version": SERVER_VERSION,
                    },
                },
            }
        )
    if method == "ping":
        return _encode({"jsonrpc": "2.0", "id": message_id, "result": {}})
    if method == "tools/list":
        return _encode(
            {"jsonrpc": "2.0", "id": message_id, "result": {"tools": _descriptors()}}
        )
    if method == "tools/call":
        return await _call_tool(message_id, params, services)
    return _error(message_id, -32601, "METHOD_NOT_FOUND")


async def run_stdio(services: Services) -> None:
    """Read newline-delimited JSON-RPC from stdin and write one frame per request.

    Every read is capped one byte over the frame limit so an oversized frame is
    detected while reading, never after buffering it. The newline counts toward
    the limit; an oversized frame is answered once and ends the session, so its
    remaining bytes cannot be re-read as later requests.
    """
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_FRAME_BYTES:
            os.write(1, _error(None, -32600, "INVALID_REQUEST"))
            return
        with quiet_console():
            try:
                message = json.loads(line)
                response = await dispatch(message, services)
            except json.JSONDecodeError:
                response = _error(None, -32700, "PARSE_ERROR")
            except Exception:
                response = _error(None, -32603, "INTERNAL_ERROR")
        if response:
            os.write(1, response)
