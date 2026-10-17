"""Loopback proof for the stdlib connector and the MCP stdio host."""

from __future__ import annotations

import asyncio
import json
import os
import select
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from spring_runtime_mcp.server import build_services
from spring_runtime_mcp.tools.inspect_beans import inspect_beans
from spring_runtime_mcp.transport.actuator_http import ActuatorHttpTransport
from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    ResponseGateReader,
)

ROOT = Path(__file__).resolve().parents[2]
BEANS = (ROOT / "tests/fixtures/bean_graph/parent-child/beans.json").read_bytes()
_ORIG_CREATE = socket.create_connection
_ORIG_GETADDRINFO = socket.getaddrinfo
_ORIG_CONNECT = socket.socket.connect
_ORIG_CONNECT_EX = socket.socket.connect_ex
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _host(address: object) -> object:
    if isinstance(address, tuple):
        return address[0]
    return address


@pytest.fixture(autouse=True)
def allow_loopback_only(
    monkeypatch: pytest.MonkeyPatch, prohibit_network: None
) -> None:
    """Undo the session network ban for this module, and only for loopback."""

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if _host(address) not in _LOOPBACK:
            raise AssertionError("Offline tests must not access the network")
        return _ORIG_CREATE(address, *args, **kwargs)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in _LOOPBACK:
            raise AssertionError("Offline tests must not access the network")
        return _ORIG_GETADDRINFO(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> Any:
        if _host(address) not in _LOOPBACK:
            raise AssertionError("Offline tests must not access the network")
        return _ORIG_CONNECT(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        if _host(address) not in _LOOPBACK:
            raise AssertionError("Offline tests must not access the network")
        return _ORIG_CONNECT_EX(self, address)

    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


class _Mount:
    def __init__(self) -> None:
        self.mode = "beans"
        self.paths: list[str] = []
        self.lock = threading.Lock()


def _handler_for(state: _Mount) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            with state.lock:
                state.paths.append(path)
                mode = state.mode
            if path == "/stolen":
                body = b"followed"
                self._send(200, "text/plain", body)
                return
            if mode == "redirect":
                host, port = self.server.server_address[:2]
                location = f"http://{host}:{port}/stolen"
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            if mode == "oversize":
                body = b"x" * 8000
                self._send(200, "application/json", body)
                return
            if path == "/actuator/beans":
                self._send(200, "application/json", BEANS)
                return
            self._send(404, "application/json", b"{}")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


@pytest.fixture
def mounted() -> Any:
    state = _Mount()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(state))
    server.handle_error = lambda *args, **kwargs: None  # type: ignore[method-assign]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, state
    finally:
        server.shutdown()
        server.server_close()


def _base(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}"


def test_real_http_transport_with_loopback_actuator(mounted: Any) -> None:
    server, _state = mounted
    services = build_services(_base(server), "local", 5.0)
    result = asyncio.run(
        inspect_beans(
            {"beanNamePattern": "orderService", "contextId": "application"},
            services,
        )
    )
    assert result["status"] in ("ok", "partial")
    assert any(item["name"] == "orderService" for item in result["data"]["items"])


def test_real_http_transport_blocks_redirect_and_oversize(mounted: Any) -> None:
    server, state = mounted
    base = _base(server)
    transport = ActuatorHttpTransport(base, targets=frozenset({"local"}))
    with pytest.raises(AssertionError, match="READ_ONLY_TRANSPORT"):
        asyncio.run(
            transport.request(
                "local",
                "beans",
                method="POST",
                follow_redirects=False,
                timeout=2,
                max_bytes=1000,
            )
        )
    with pytest.raises(AssertionError, match="READ_ONLY_TRANSPORT"):
        asyncio.run(
            transport.request(
                "local",
                "beans",
                method="GET",
                follow_redirects=True,
                timeout=2,
                max_bytes=1000,
            )
        )
    assert state.paths == []

    state.mode = "redirect"
    reader = ResponseGateReader(
        transport,
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans"}),
        timeout=2,
    )
    with pytest.raises(EndpointReadError) as captured:
        asyncio.run(reader.read("local", "beans", snapshot_id="redirect-snap"))
    assert captured.value.code == "REDIRECT_BLOCKED"
    assert captured.value.http_status == 302
    assert state.paths == ["/actuator/beans"]

    state.mode = "oversize"
    state.paths.clear()
    response = asyncio.run(
        transport.request(
            "local",
            "beans",
            method="GET",
            follow_redirects=False,
            timeout=2,
            max_bytes=1000,
        )
    )
    assert response.status == 200
    assert len(response.body) == 1001
    assert state.paths == ["/actuator/beans"]


def _frame(process: subprocess.Popen[bytes], payload: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
    process.stdin.flush()
    ready, _, _ = select.select([process.stdout], [], [], 5)
    assert ready, "timed out waiting for an MCP frame"
    line = process.stdout.readline()
    assert line
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def test_mcp_stdio_lifecycle_drift_and_unknown_tool(mounted: Any) -> None:
    server, _state = mounted
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "spring_runtime_mcp.cli",
            "--base-url",
            _base(server),
            "--timeout",
            "5",
        ],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        initialized = _frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "audit", "version": "0"},
                },
            },
        )
        assert initialized["id"] == 1
        assert initialized["result"]["protocolVersion"] == "2026-07-28"
        assert initialized["result"]["serverInfo"]["name"] == "spring-runtime-mcp"
        assert process.stdin is not None
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        listed = _frame(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [item["name"] for item in listed["result"]["tools"]]
        assert names == sorted(names) and len(names) == 8
        assert "springboot_inspect_beans" in names
        assert "springboot_diagnose_health" in names

        beans = _frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "springboot_inspect_beans",
                    "arguments": {
                        "beanNamePattern": "orderService",
                        "contextId": "application",
                    },
                },
            },
        )
        assert beans["result"]["isError"] is False
        found = beans["result"]["structuredContent"]["data"]["items"]
        assert any(item["name"] == "orderService" for item in found)

        drift = _frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "springboot_detect_drift",
                    "arguments": {"expectedBeans": [{"name": "orderService"}]},
                },
            },
        )
        assert drift["result"]["isError"] is False
        diagnosis = drift["result"]["structuredContent"]
        assert diagnosis["status"] in ("ok", "partial")
        assert diagnosis["data"]["missingInRuntime"] == []
        assert diagnosis["data"]["profileMismatch"] is None
        unknown = _frame(
            process,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "not_a_spring_tool",
                    "arguments": {},
                },
            },
        )
        assert unknown["result"]["isError"] is True
        rejected = unknown["result"]["structuredContent"]
        assert rejected["status"] == "unavailable"
        assert rejected["data"] is None
        assert rejected["errors"][0]["code"] == "UNKNOWN_TOOL"
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr else b""
        assert b"Traceback" not in stderr
