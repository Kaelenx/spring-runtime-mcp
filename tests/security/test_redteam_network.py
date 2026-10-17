import asyncio

import pytest
from jsonschema import Draft202012Validator

from spring_runtime_mcp.contracts.definitions import definition, list_tools
from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.replay import services_for
from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    HttpResponse,
    ResponseGateReader,
)

from .helpers import HOSTS, SpyTransport, run_handler


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("field", ["url", "origin", "baseUrl", "proxy", "headers"])
def test_network_tool_parameters_never_reach_reader(host, field):
    services = services_for("parent-child")
    result = run_handler(
        {"beanNamePattern": "*", field: "http://" + host + "/"}, services
    )
    assert services.reader.calls == []
    assert result["errors"][0]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("host", HOSTS)
def test_network_unregistered_target_never_resolves_or_requests(host):
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )
    with pytest.raises(EndpointReadError):
        asyncio.run(reader.read("http://" + host, "beans", snapshot_id="snapshot"))
    assert spy.calls == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_network_redirect_status_is_not_followed(status):
    spy = SpyTransport(
        HttpResponse(status, "text/html", b"<html>redirect to metadata</html>")
    )
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )
    with pytest.raises(EndpointReadError, match="REDIRECT_BLOCKED"):
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))
    assert len(spy.calls) == 1 and spy.calls[0][2]["follow_redirects"] is False


def test_network_policy_config_is_frozen_and_validated():
    targets, endpoints = {"local"}, {"beans"}
    spy = SpyTransport()
    reader = ResponseGateReader(spy, targets=targets, enabled_endpoints=endpoints)
    targets.add("other")
    endpoints.add("http://169.254.169.254/")
    for target, endpoint in (("other", "beans"), ("local", "http://169.254.169.254/")):
        with pytest.raises(EndpointReadError):
            asyncio.run(reader.read(target, endpoint, snapshot_id="snapshot"))
    assert spy.calls == []


@pytest.mark.parametrize(
    "target", ["http://169.254.169.254/", "local\n", "local:8080", "", "local/user"]
)
def test_network_invalid_registered_id_is_not_an_origin(target):
    with pytest.raises((ValueError, NormalizationError)):
        ResponseGateReader(
            SpyTransport(),
            targets=frozenset({target}),
            enabled_endpoints=frozenset({"beans"}),
        )


def test_network_all_tool_schemas_deny_dynamic_host_escalation():
    for tool in list_tools():
        schema = definition(tool["name"])["inputSchema"]
        assert schema["additionalProperties"] is False
        for host in HOSTS:
            assert not Draft202012Validator(schema).is_valid({"url": "http://" + host})


def test_network_rebinding_input_has_no_dns_or_socket_surface(monkeypatch):
    import socket

    calls = []

    def forbidden_resolution(*args, **kwargs):
        calls.append(args)
        # Would switch from loopback to metadata if a second lookup existed.
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1" if len(calls) == 1 else "169.254.169.254", 80),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_resolution)
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )
    with pytest.raises(EndpointReadError):
        asyncio.run(reader.read("rebind.example.test", "beans", snapshot_id="snapshot"))
    assert calls == [] and spy.calls == []


@pytest.mark.parametrize(
    "response",
    [
        HttpResponse(True, "application/json", b"{}"),
        HttpResponse("password=synthetic", "application/json", b"{}"),
        HttpResponse(200, None, b"{}"),
        HttpResponse(200, "application/json", "not-bytes"),
    ],
)
def test_network_malformed_transport_metadata_is_a_structured_failure(response):
    spy = SpyTransport(response)
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )
    with pytest.raises(EndpointReadError, match="INVALID_HTTP_RESPONSE"):
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))


def test_network_unexpected_library_exception_never_exports_client_message():
    spy = SpyTransport(error=RuntimeError("password=synthetic-library-secret"))
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )
    with pytest.raises(EndpointReadError, match="TARGET_CONNECTION_FAILED") as captured:
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))
    assert "synthetic-library-secret" not in str(captured.value)
