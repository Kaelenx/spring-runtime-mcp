"""Contract assertions on actual server frames, with offline connection faults."""

from __future__ import annotations

import asyncio
import errno
import http.client
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from spring_runtime_mcp.server import build_services

from harness.host import dispatch
from spring_runtime_mcp.tools.inspect_beans import Services
from spring_runtime_mcp.policy.configuration import ConfigReadPolicy

from .assertions import NAMES, validator

SECRET = "synthetic-server-contract-credential"
TOOL_CALLS = (
    ("springboot_analyze_startup", {}, "startup"),
    ("springboot_check_migrations", {"engine": "flyway"}, "flyway"),
    ("springboot_detect_drift", {"expectedBeans": [{"name": "orderService"}]}, "beans"),
    ("springboot_diagnose_health", {}, "health"),
    ("springboot_inspect_beans", {"beanNamePattern": "orderService"}, "beans"),
    ("springboot_inspect_config", {"prefix": "spring.application"}, "env"),
    ("springboot_search_openapi", {"schemaName": "Address"}, "openapi"),
    ("springboot_verify_mappings", {"pathPattern": "/orders"}, "mappings"),
)


@pytest.fixture
def unreachable_services(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Services, list[tuple[str, int]]]:
    connections: list[tuple[str, int]] = []

    def refused(connection: http.client.HTTPConnection) -> None:
        connections.append((connection.host, connection.port))
        raise ConnectionRefusedError(errno.ECONNREFUSED, f"Bearer {SECRET}")

    # Keep the production connector and gate; fail before any socket is opened.
    monkeypatch.setattr(http.client.HTTPConnection, "connect", refused)
    # This fixture tests connection failure after explicit authorization, not
    # the default-disabled configuration capability (covered by TC-BUG-02).
    return build_services(
        "http://127.0.0.1:18080",
        config_policy=ConfigReadPolicy(prefixes=frozenset({"spring.application"})),
    ), connections


def test_server_tools_list_exposes_valid_schemas_without_connecting(
    unreachable_services: tuple[Services, list[tuple[str, int]]],
) -> None:
    services, connections = unreachable_services
    frame = asyncio.run(
        dispatch({"jsonrpc": "2.0", "id": "listing", "method": "tools/list"}, services)
    )
    assert frame is not None
    response = json.loads(frame)
    assert response["jsonrpc"] == "2.0" and response["id"] == "listing"
    tools = response["result"]["tools"]
    assert tuple(tool["name"] for tool in tools) == NAMES
    arguments = {name: values for name, values, _endpoint in TOOL_CALLS}
    for tool in tools:
        assert isinstance(tool["description"], str) and tool["description"]
        schema = tool["inputSchema"]
        Draft202012Validator.check_schema(schema)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        Draft202012Validator(schema).validate(arguments[tool["name"]])
        assert schema["properties"]["maxResults"]["default"] == 50
        assert schema["properties"]["maxResults"]["maximum"] == 50
        for hint, expected in {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }.items():
            assert tool["annotations"][hint] is expected
    assert connections == []


@pytest.mark.parametrize("tool,arguments,endpoint", TOOL_CALLS)
def test_server_unreachable_target_returns_structured_error(
    unreachable_services: tuple[Services, list[tuple[str, int]]],
    tool: str,
    arguments: dict[str, Any],
    endpoint: str,
) -> None:
    services, connections = unreachable_services
    frame = asyncio.run(
        dispatch(
            {
                "jsonrpc": "2.0",
                "id": "unreachable",
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            services,
        )
    )
    assert frame is not None
    assert len(frame.rstrip(b"\n")) <= 16_384
    response = json.loads(frame)
    assert response["jsonrpc"] == "2.0" and response["id"] == "unreachable"
    result = response["result"]
    assert result["isError"] is True
    envelope = result["structuredContent"]
    validator(tool).validate(envelope)
    assert len(result["content"]) == 1
    assert result["content"][0]["type"] == "text"
    assert json.loads(result["content"][0]["text"]) == envelope
    assert envelope["status"] == "unavailable"
    assert envelope["data"] is None
    assert envelope["totalMatched"] is None
    assert envelope["truncated"] is False
    assert envelope["coverage"]["state"] == "unavailable"
    assert envelope["nextAction"]["kind"] == "remediate"
    assert envelope["nextAction"]["reasonCode"] == "TARGET_CONNECTION_REFUSED"
    diagnostic = envelope["errors"][0]
    assert diagnostic["code"] == "TARGET_CONNECTION_REFUSED"
    assert diagnostic["endpointId"] == endpoint
    assert diagnostic["httpStatus"] is None
    assert diagnostic["remediation"]["action"] == "reviewTarget"
    assert SECRET.encode() not in frame
    assert b"Traceback" not in frame
    assert connections and set(connections) == {("127.0.0.1", 18080)}
