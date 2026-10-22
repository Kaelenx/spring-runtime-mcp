import asyncio

import pytest

from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    ResponseGateReader,
)

from .helpers import SpyTransport

DANGEROUS = [
    "shutdown",
    "heapdump",
    "logfile",
    "sessions",
    "caches",
    "loggers",
    "quartz",
]


@pytest.mark.parametrize(
    "endpoint",
    [
        "beans",
        "conditions",
        "health",
        "mappings",
        "flyway",
        "liquibase",
        "startup",
        "openapi",
    ],
)
def test_readonly_every_builtin_request_is_get_without_redirects(endpoint):
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({endpoint})
    )
    asyncio.run(reader.read("local", endpoint, snapshot_id="snapshot"))
    assert spy.calls[0][2]["method"] == "GET"
    assert spy.calls[0][2]["follow_redirects"] is False


@pytest.mark.parametrize("endpoint", DANGEROUS)
@pytest.mark.parametrize(
    "template",
    [
        "{}",
        "/actuator/{}",
        "../{}",
        "beans/../{}",
        "beans%2f..%2f{}",
        "beans;%2f{}",
        "{}?x=1",
    ],
)
def test_readonly_dangerous_paths_and_aliases_never_reach_transport(endpoint, template):
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy,
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans", "startup"}),
    )
    with pytest.raises(EndpointReadError):
        asyncio.run(
            reader.read("local", template.format(endpoint), snapshot_id="snapshot")
        )
    assert spy.calls == []


def test_readonly_policy_reassignment_cannot_enable_endpoint():
    reader = ResponseGateReader(
        SpyTransport(),
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans"}),
    )
    with pytest.raises((AttributeError, TypeError)):
        reader.enabled_endpoints = frozenset({"heapdump"})


def test_readonly_in_place_configuration_merge_cannot_bypass_hard_allowlist():
    endpoints = {"beans"}
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=endpoints
    )
    endpoints.update({"startup", "shutdown"})
    for endpoint in ("startup", "shutdown"):
        with pytest.raises(EndpointReadError):
            asyncio.run(reader.read("local", endpoint, snapshot_id="snapshot"))
    assert spy.calls == []


def test_readonly_static_inventory_has_one_request_constructor_and_no_execution_sinks():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src/spring_runtime_mcp"
    request_calls = []
    forbidden = {
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "http.client",
        "urllib.request",
        "urllib3",
        "subprocess",
        # The official MCP SDK owns the stdio transport; it must never appear
        # inside the analysis layers, only at the host boundary it serves.
        "mcp",
    }

    def blocked(module: str | None, relative: str) -> bool:
        # The one production connector resolves a numeric address and verifies
        # the real peer before any HTTP write, so it alone may import socket
        # and http.client. Every other network stack stays banned.
        if relative in {"server.py", "cli.py"} and module in {"mcp", "mcp_types"}:
            return False
        if relative == "transport/actuator_http.py" and module in {
            "http.client",
            "socket",
        }:
            return False
        return module in forbidden

    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(blocked(alias.name, relative) for alias in node.names), (
                    path
                )
            if isinstance(node, ast.ImportFrom):
                assert not blocked(node.module, relative), path
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in {"print", "eval", "exec", "compile"}, (
                        path
                    )
                if isinstance(node.func, ast.Attribute):
                    # SnapshotStore.put is a local cache operation, verified
                    # separately by the cache rejection tests, not HTTP PUT.
                    cache_put = (
                        node.func.attr == "put"
                        and isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == "snapshots"
                        and path.name in {"inspect_beans.py", "support.py"}
                    )
                    assert cache_put or node.func.attr not in {
                        "post",
                        "put",
                        "patch",
                        "delete",
                        "system",
                        "popen",
                        "send",
                        "sendall",
                        "sendto",
                        "sendmsg",
                    }, path
                    if node.func.attr == "request":
                        request_calls.append((path.name, node))
    by_file: dict[str, list[ast.Call]] = {}
    for filename, node in request_calls:
        by_file.setdefault(filename, []).append(node)
    assert set(by_file) == {"response_gate.py", "actuator_http.py"}
    assert len(by_file["response_gate.py"]) == 1
    assert len(by_file["actuator_http.py"]) == 1
    for node in (*by_file["response_gate.py"], *by_file["actuator_http.py"]):
        policy = {kw.arg: kw.value for kw in node.keywords}
        assert (
            isinstance(policy["method"], ast.Constant)
            and policy["method"].value == "GET"
        )
    gate_policy = {kw.arg: kw.value for kw in by_file["response_gate.py"][0].keywords}
    assert (
        isinstance(gate_policy["follow_redirects"], ast.Constant)
        and gate_policy["follow_redirects"].value is False
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_readonly_no_internal_method_override(method):
    spy = SpyTransport()
    reader = ResponseGateReader(
        spy, targets=frozenset({"local"}), enabled_endpoints=frozenset({"startup"})
    )
    with pytest.raises(TypeError):
        asyncio.run(
            reader.read("local", "startup", snapshot_id="snapshot", method=method)
        )
    assert not spy.calls
