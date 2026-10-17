"""Fault injection drives the real policy gate; no fake socket or live target."""

import asyncio
import errno
import json

import pytest

from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services
from spring_runtime_mcp.transport import response_gate
from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    HttpResponse,
    ResponseGateReader,
)

from .assertions import assert_contract

FAULTS = {
    "connection-refused": ("TARGET_CONNECTION_REFUSED", "reviewTarget"),
    "read-timeout": ("TARGET_TIMEOUT", "reviewTarget"),
    "not-exposed": ("ENDPOINT_NOT_EXPOSED", "reviewExposure"),
    "startup-unavailable": ("STARTUP_DATA_UNAVAILABLE", "configureStartup"),
    "unauthorized": ("ENDPOINT_UNAUTHORIZED", "reviewAuthorization"),
    "forbidden": ("ENDPOINT_UNAUTHORIZED", "reviewAuthorization"),
    "login-redirect": ("REDIRECT_BLOCKED", "reviewAuthorization"),
}


class ScriptedTransport:
    def __init__(self, case):
        self.case = case
        self.calls = []

    async def request(self, target_id, endpoint_id, **policy):
        self.calls.append((target_id, endpoint_id, policy))
        if self.case.get("error"):
            number = (
                errno.ECONNREFUSED
                if self.case["error"] == "ECONNREFUSED"
                else errno.ETIMEDOUT
            )
            raise OSError(
                number, "synthetic upstream error containing Bearer must-not-escape"
            )
        body = self.case["body"]
        return HttpResponse(
            self.case["status"],
            self.case["contentType"],
            body.encode() if isinstance(body, str) else json.dumps(body).encode(),
        )


def gate_for(case):
    transport = ScriptedTransport(case)
    reader = ResponseGateReader(
        transport,
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({case["endpointId"]}),
    )
    return reader, transport


TOOL_CALLS = (
    ("springboot_analyze_startup", {}, "startup"),
    ("springboot_check_migrations", {"engine": "flyway"}, "flyway"),
    ("springboot_detect_drift", {"expectedBeans": [{"name": "orderService"}]}, "beans"),
    ("springboot_diagnose_health", {}, "health"),
    ("springboot_inspect_beans", {"beanNamePattern": "*"}, "beans"),
    ("springboot_inspect_config", {"prefix": "spring"}, "env"),
    ("springboot_search_openapi", {"pathPattern": "/orders"}, "openapi"),
    ("springboot_verify_mappings", {"pathPattern": "/*"}, "mappings"),
)


@pytest.mark.parametrize("case_id", FAULTS)
@pytest.mark.parametrize(("tool", "arguments", "endpoint"), TOOL_CALLS)
def test_every_tool_error_envelope_conforms(
    tool, arguments, endpoint, case_id, load_sample
):
    from spring_runtime_mcp.tools.registry import call_tool

    case = dict(next(c for c in load_sample("faults/cases.json") if c["id"] == case_id))
    case["endpointId"] = endpoint
    reader, transport = gate_for(case)
    services = Services(
        reader,
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )
    result = asyncio.run(call_tool(tool, arguments, services))
    assert_contract(tool, result)
    assert result["data"] is None and result["totalMatched"] is None
    diagnostic = result["errors"][0]
    expected_code, expected_action = FAULTS[case_id]
    if endpoint == "startup" and case.get("status") == 404:
        expected_code, expected_action = "STARTUP_DATA_UNAVAILABLE", "configureStartup"
    elif case_id == "startup-unavailable":
        expected_code, expected_action = "ENDPOINT_NOT_EXPOSED", "reviewExposure"
    assert diagnostic["code"] == expected_code
    assert diagnostic["endpointId"] == endpoint
    assert diagnostic["httpStatus"] == case.get("status")
    assert diagnostic["remediation"]["action"] == expected_action
    assert "must-not-escape" not in json.dumps(result)
    assert len(transport.calls) == 1
    policy = transport.calls[0][2]
    assert policy["method"] == "GET" and policy["follow_redirects"] is False
    assert policy["timeout"] == 5 and policy["max_bytes"] == 8 * 1024 * 1024
    if expected_code == "ENDPOINT_NOT_EXPOSED":
        assert (
            "management.endpoints.web.exposure.include"
            in diagnostic["remediation"]["configKeys"]
        )
    if expected_code == "STARTUP_DATA_UNAVAILABLE":
        assert "BufferingApplicationStartup" in diagnostic["remediation"]["steps"][0]
        assert diagnostic["remediation"]["requiresApproval"] is True


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404])
def test_redirect_and_security_html_never_reaches_json_parser(status, monkeypatch):
    def parse_forbidden(*args, **kwargs):
        raise AssertionError("Must inspect status before parsing HTML or JSON")

    monkeypatch.setattr(response_gate, "loads_bounded", parse_forbidden)
    reader, transport = gate_for(
        {
            "endpointId": "beans",
            "status": status,
            "contentType": "text/html",
            "body": "<html>login</html>",
        }
    )
    with pytest.raises(EndpointReadError):
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))
    assert len(transport.calls) == 1


def test_html_success_is_structured_content_type_error():
    reader, _ = gate_for(
        {
            "endpointId": "beans",
            "status": 200,
            "contentType": "text/html",
            "body": "<html>login</html>",
        }
    )
    with pytest.raises(EndpointReadError, match="UNEXPECTED_CONTENT_TYPE"):
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))


def test_hidden_health_does_not_invent_components(load_sample):
    case = next(
        c for c in load_sample("faults/cases.json") if c["id"] == "health-hidden"
    )
    reader, _ = gate_for(case)
    observation = asyncio.run(reader.read("local", "health", snapshot_id="snapshot"))
    assert observation.body == {"status": "DOWN"}
    result = load_sample("faults/health-hidden.expected.json")
    assert_contract("springboot_diagnose_health", result)
    assert result["status"] == "partial" and result["data"]["overallStatus"] == "DOWN"
    assert result["data"]["items"][0]["componentPath"] == []
    assert result["data"]["items"][0]["childrenComplete"] is False
    assert result["nextAction"]["reasonCode"] == "HEALTH_DETAILS_NOT_VISIBLE"


def test_disabled_capability_and_unknown_target_do_not_call_transport():
    reader, transport = gate_for({"endpointId": "beans"})
    for target, endpoint in (
        ("other", "beans"),
        ("local", "startup"),
        ("local", "http://169.254.169.254"),
    ):
        with pytest.raises(EndpointReadError):
            asyncio.run(reader.read(target, endpoint, snapshot_id="snapshot"))
    assert not transport.calls


@pytest.mark.parametrize(
    "body,code",
    [
        (b'{"x":1,"x":2}', "DUPLICATE_JSON_KEY"),
        (b"{bad", "INVALID_JSON"),
        (b" " * (8 * 1024 * 1024 + 1), "RESPONSE_TOO_LARGE"),
    ],
)
def test_bounded_response_parser_faults(body, code):
    class Transport:
        async def request(self, *args, **kwargs):
            return HttpResponse(200, "application/json", body)

    reader = ResponseGateReader(
        Transport(),
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans"}),
    )
    with pytest.raises(EndpointReadError, match=code):
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))


@pytest.mark.parametrize("representation", ["v2", "v3"])
def test_actuator_media_type_negotiation(representation):
    reader, _ = gate_for(
        {
            "endpointId": "health",
            "status": 200,
            "contentType": f"application/vnd.spring-boot.actuator.{representation}+json; charset=utf-8",
            "body": {"status": "DOWN"},
        }
    )
    observation = asyncio.run(reader.read("local", "health", snapshot_id="snapshot"))
    assert observation.representation == (
        "actuatorV2" if representation == "v2" else "actuatorV3"
    )


def test_offline_guard_rejects_dns_and_socket_connect():
    import socket

    with pytest.raises(AssertionError, match="Offline tests"):
        socket.getaddrinfo("localhost", 8080)
    with (
        socket.socket() as connection,
        pytest.raises(AssertionError, match="Offline tests"),
    ):
        connection.connect(("127.0.0.1", 8080))
