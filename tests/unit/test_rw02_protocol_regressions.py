"""Correct-behavior regressions for the RW-02 protocol/online-contract chain.

These encode SPEC.md section 2.2 and the MCP 2026-07-28 server/tools error
contract: the server must publish the committed outputSchema, reject malformed
envelopes before any handler runs, answer unknown tools with a JSON-RPC
protocol error, negotiate an actually supported protocol version, and never
emit a structured result that violates its own outputSchema. Expected to fail
until the RW-02 implementation lands.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.host import dispatch as sdk_dispatch
from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services
from spring_runtime_mcp.tools.registry import HANDLERS

CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "springboot_diagnose_health", "arguments": {}},
}


class ProbeReader:
    """Fail the test if any protocol gate lets a handler read an endpoint."""

    def __init__(self) -> None:
        self.calls = 0

    async def read(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("protocol gate reached the reader")


def probe_services() -> tuple[Services, ProbeReader]:
    reader = ProbeReader()
    return (
        Services(
            reader,
            SourceCatalog("local", None, {}, (), ()),
            SanitizerPipeline(),
            SnapshotStore(),
        ),
        reader,
    )


def dispatch(message: object, *, expect_reply: bool = True) -> dict[str, Any] | None:
    services, reader = probe_services()
    frame = asyncio.run(sdk_dispatch(message, services, expect_reply=expect_reply))
    assert reader.calls == 0, "protocol gate allowed a handler to perform IO"
    return None if frame is None else json.loads(frame)


def test_rw02_tools_list_publishes_the_committed_tool_contract() -> None:
    """RW-02: tools/list must republish each contract entry, outputSchema included."""
    body = dispatch({"jsonrpc": "2.0", "id": "listing", "method": "tools/list"})
    assert body is not None
    tools = body["result"]["tools"]
    assert [tool["name"] for tool in tools] == sorted(tool["name"] for tool in tools)
    for tool in tools:
        expected = definition(tool["name"])
        assert tool.get("outputSchema") == expected["outputSchema"], (
            "RW-02: server.py _descriptors() omitted or altered the published "
            f"outputSchema for {tool['name']}"
        )
        assert set(tool) == set(expected), (tool["name"], sorted(tool))


@pytest.mark.parametrize(
    "case,params,answered",
    [
        ("unknown-tool", {"name": "springboot_missing_tool", "arguments": {}}, True),
        ("params-not-object", [], False),
    ],
)
def test_rw02_unknown_tool_and_bad_params_are_protocol_errors(
    case: str, params: Any, answered: bool
) -> None:
    """RW-02: unknown tools and malformed call params never run a handler.

    Unknown names get -32602; params that do not fit the request shape are
    dropped by the SDK transport without a reply.
    """
    body = dispatch(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
        expect_reply=answered,
    )
    if body is None:
        # The SDK transport drops frames whose params do not fit the request
        # shape; the security property is rejection before any IO.
        return
    assert "result" not in body, (
        f"RW-02: {case} was reported as a tool execution result instead of a "
        f"protocol error: {body}"
    )
    assert body["error"]["code"] == -32602, body["error"]


@pytest.mark.parametrize(
    "envelope",
    [
        {"id": 1, "method": "ping"},
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": 2.0, "id": 1, "method": "ping"},
        {**CALL, "jsonrpc": "1.0"},
    ],
    ids=["missing", "legacy", "number", "call-legacy"],
)
def test_rw02_invalid_jsonrpc_is_rejected_before_handlers(
    envelope: dict[str, Any],
) -> None:
    """RW-02: a frame that is not JSON-RPC 2.0 never reaches a handler.

    The official SDK transport drops unparsable frames without a reply; the
    security property under test is rejection before any IO, not a -32600 body.
    """
    body = dispatch(envelope, expect_reply=False)
    assert body is None or body.get("error", {}).get("code") == -32600, body


@pytest.mark.parametrize(
    "request_id", [[], {}, True, 1.5], ids=["array", "object", "boolean", "float"]
)
def test_rw02_invalid_request_ids_are_rejected_before_handlers(
    request_id: Any,
) -> None:
    """RW-02: an unusable id must fail the envelope, not be discovered after IO."""
    body = dispatch({**CALL, "id": request_id}, expect_reply=False)
    if body is None:
        return  # transport-level rejection without a reply
    assert body["error"]["code"] == -32600, body
    assert body["id"] is None, body


def test_rw02_initialize_never_echoes_an_unsupported_version() -> None:
    """RW-02: an unsupported version must never be accepted or echoed.

    Era-agnostic: a legacy-style handshake must answer with a version it
    supports, and a modern (2026-07-28) server must reject with
    UnsupportedProtocolVersionError listing the versions it does support.
    """
    requested = "1999-01-01"
    body = dispatch(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": requested,
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"},
            },
        }
    )
    assert body is not None
    if "result" in body:
        version = body["result"]["protocolVersion"]
        assert isinstance(version, str) and 0 < len(version) <= 64, version
        assert version != requested, (
            "RW-02: server.py echoed an unsupported protocolVersion instead of "
            f"negotiating: {version}"
        )
        return
    error = body["error"]
    assert error["code"] in {-32022, -32600, -32601, -32602}, error
    supported = (error.get("data") or {}).get("supported")
    if supported is not None:
        assert isinstance(supported, list) and supported, supported
        assert requested not in supported, supported


def test_rw02_output_contract_violation_becomes_a_safe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RW-02: structuredContent must satisfy its own outputSchema before egress."""

    async def broken(arguments: Any, services: Any) -> dict[str, Any]:
        return {"schemaVersion": "2", "status": "ok"}

    monkeypatch.setitem(HANDLERS, "springboot_diagnose_health", broken)
    body = dispatch(CALL)
    assert body is not None and "result" in body, body
    result = body["result"]
    envelope = result["structuredContent"]
    name = "springboot_diagnose_health"
    Draft202012Validator(definition(name)["outputSchema"]).validate(envelope)
    assert result["isError"] is True, (
        "RW-02: an envelope that violates its outputSchema was returned as a "
        f"successful result: {envelope}"
    )
    assert envelope["status"] in {"invalid", "unavailable"}, envelope
    assert envelope["errors"], envelope
