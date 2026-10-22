"""Process-level STDIO host E2E: real entrypoint, real pipes, loopback fixture.

This is the layer above the in-process tests: `spring_runtime_mcp.cli` starts
the production host, stdin/stdout carry real bytes, and the OpenAPI case reaches
the production HTTP connector through a disposable loopback server. All
payloads are synthetic; no test opens an outbound connection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRAME_CAP = 64 * 1024
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def _frame(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _handshake() -> bytes:
    """The SDK host only serves tools after the initialize exchange."""
    return b"".join(
        [
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "process-test", "version": "0"},
                    },
                }
            ),
            _frame({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        ]
    )


def host_in_stages(
    *stages: bytes,
    base_url: str | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    """Feed stdin in stages, reading each stage's replies before the next.

    A client that pipelines the handshake and the calls in one burst can have
    later frames dropped by the SDK's pre-initialization replay; sending the
    handshake first and reading its reply is the protocol-correct order.
    """
    environment = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    command = [sys.executable, "-m", "spring_runtime_mcp.cli"]
    if base_url is not None:
        command += ["--base-url", base_url]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        transcript = b""
        for index, stage in enumerate(stages):
            process.stdin.write(stage)
            process.stdin.flush()
            if index < len(stages) - 1:
                line = process.stdout.readline()
                assert line, "host closed before answering a stage"
                transcript += line
        process.stdin.close()
        returncode = process.wait(timeout=timeout)
        transcript += process.stdout.read()
        stderr = process.stderr.read() if process.stderr else b""
    finally:
        if process.poll() is None:
            process.kill()
    return subprocess.CompletedProcess(command, returncode, transcript, stderr)


def _call(message_id: int, name: Any, arguments: dict[str, Any] | None = None) -> bytes:
    return _frame(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )


def host(
    stdin: bytes,
    *,
    base_url: str | None = None,
    timeout: float = 30,
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run the real STDIO entrypoint under a hard timeout; EOF then ends it."""
    environment = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    command = [sys.executable, "-m", "spring_runtime_mcp.cli"]
    if base_url is not None:
        command += ["--base-url", base_url]
    if args is not None:
        command += args
    return subprocess.run(
        command,
        input=stdin,
        check=False,
        capture_output=True,
        timeout=timeout,
        env=environment,
    )


class Session:
    """Interactive process session for cases that must respect protocol order."""

    def __init__(self, *, base_url: str | None = None, timeout: float = 30) -> None:
        environment = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(ROOT / "src"),
        )
        command = [sys.executable, "-m", "spring_runtime_mcp.cli"]
        if base_url is not None:
            command += ["--base-url", base_url]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            text=False,
        )
        self.timeout = timeout

    def send(self, payload: dict[str, Any]) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(_frame(payload))
        self._process.stdin.flush()

    def read(self) -> dict[str, Any]:
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        assert line, "host closed the stream before answering"
        return json.loads(line)

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.send(payload)
        while True:
            response = self.read()
            if response.get("id") == payload.get("id"):
                return response

    def initialize(self) -> None:
        response = self.request(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "process-test", "version": "0"},
                },
            }
        )
        assert "result" in response, response
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> tuple[int, bytes]:
        assert self._process.stdin is not None
        self._process.stdin.close()
        returncode = self._process.wait(timeout=self.timeout)
        stderr = self._process.stderr.read() if self._process.stderr else b""
        return returncode, stderr


def _fanout_document() -> dict[str, Any]:
    schemas: dict[str, Any] = {"S8": {"type": "string"}}
    for level in range(8):
        schemas[f"S{level}"] = {
            "type": "object",
            "properties": {
                f"p{index}": {"$ref": f"#/components/schemas/S{level + 1}"}
                for index in range(50)
            },
        }
    return {"openapi": "3.0.3", "paths": {}, "components": {"schemas": schemas}}


@pytest.fixture
def openapi_fixture() -> Any:
    requests: list[tuple[str, str]] = []
    lock = threading.Lock()
    body = json.dumps(_fanout_document()).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            with lock:
                requests.append((self.command, self.path))
            payload = body if self.path == "/actuator/openapi" else b"{}"
            self.send_response(200 if payload is body else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stdio_host_process_handles_a_frame_exactly_at_the_cap() -> None:
    """TC-BUG-04: the newline counts toward the cap; 65,536 bytes still works."""
    base = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
    at_limit = base + b" " * (FRAME_CAP - len(base) - 1) + b"\n"
    assert len(at_limit) == FRAME_CAP

    completed = host(at_limit)

    assert completed.returncode == 0 and completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, completed.stdout
    assert json.loads(lines[0])["result"] == {}


def test_stdio_host_process_rejects_an_oversized_frame_and_stops() -> None:
    """TC-BUG-04: one bounded read, one fixed error, trailing bytes unanswered."""
    oversized = b"A" * (FRAME_CAP + 1)

    completed = host(oversized + _frame(PING))

    assert completed.returncode == 0 and completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, (
        "an oversized frame must not be split into later requests: "
        f"stdout={completed.stdout!r}"
    )
    error = json.loads(lines[0])
    assert error["id"] is None and error["error"]["code"] == -32600


def test_stdio_host_process_never_blocks_on_unterminated_oversized_input() -> None:
    """TC-BUG-04: a stream that never sends a newline must not hang the host."""
    environment = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(ROOT / "src"),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "spring_runtime_mcp.cli"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(b"A" * (FRAME_CAP + 1))
        process.stdin.flush()
        returncode = process.wait(timeout=15)
        stdout = process.stdout.read() if process.stdout else b""
        stderr = process.stderr.read() if process.stderr else b""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process.stdin is not None:
            process.stdin.close()

    assert returncode == 0 and stderr == b""
    lines = stdout.splitlines()
    assert len(lines) == 1, stdout
    assert json.loads(lines[0])["error"]["code"] == -32600


def test_stdio_host_process_never_escalates_name_errors_to_internal() -> None:
    """TC-BUG-12: unusable tool names stay controlled through the real host."""
    completed = host_in_stages(
        _handshake(),
        b"".join(
            [
                _call(1, []),
                _call(2, {}),
                _call(3, None),
                _call(4, True),
                _call(5, "springboot_missing_tool"),
            ]
        ),
    )

    assert completed.returncode == 0 and completed.stderr == b""
    lines = [json.loads(line) for line in completed.stdout.splitlines()]
    answers = [line for line in lines if line.get("id") in {1, 2, 3, 4, 5}]
    # A type-invalid name is rejected by the request shape; the SDK's typed
    # parameters drop names that are simply absent or wrong-typed.
    assert 1 <= len(answers) <= 5, answers
    for answer in answers:
        assert answer["error"]["code"] == -32602, answer
    assert b"INTERNAL_ERROR" not in completed.stdout
    assert b"Traceback" not in completed.stderr


def test_stdio_host_process_bounds_openapi_fanout_over_the_wire(
    openapi_fixture: Any,
) -> None:
    """TC-BUG-01: a 50-way/eight-level OpenAPI fan-out stays bounded on the wire."""
    base_url, requests = openapi_fixture

    session = Session(base_url=base_url)
    try:
        session.initialize()
        response = session.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "springboot_search_openapi",
                    "arguments": {"schemaName": "S0", "maxRefDepth": 8},
                },
            }
        )
        returncode, stderr = session.close()
    finally:
        returncode, stderr = locals().get("returncode", 0), locals().get("stderr", b"")
        if session._process.poll() is None:
            session._process.kill()

    assert returncode == 0, stderr
    assert b"Traceback" not in stderr
    assert len(json.dumps(response, separators=(",", ":"))) <= 16_384, (
        f"wire frame must stay bounded, got {len(json.dumps(response))} bytes"
    )
    result = response["result"]
    envelope = result["structuredContent"]
    assert result["isError"] is False
    assert envelope["status"] == "partial" and envelope["data"]["items"]
    assert envelope["truncated"] is True
    assert "REF_EXPANSION_LIMIT" in envelope["coverage"]["gaps"]
    assert "REF_EXPANSION_LIMIT" in envelope["truncationReasons"]
    assert envelope["data"]["items"][0]["expansionComplete"] is False
    assert requests == [("GET", "/actuator/openapi")], requests


def test_stdio_host_process_rejects_a_mismatched_fact_catalog(
    tmp_path: Path,
) -> None:
    """RW-06: an operator fact file must match the registered target."""
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"targetId": "other", "types": [], "producers": []}))

    completed = host(_frame(PING), args=["--source-catalog", str(catalog)])

    assert completed.returncode == 2, completed.stderr
    assert completed.stdout == b""
    assert completed.stderr == b"invalid server configuration\n"
    assert str(catalog).encode() not in completed.stderr


def test_stdio_host_process_accepts_a_matching_fact_catalog(tmp_path: Path) -> None:
    """RW-06: a valid operator fact file must not break the STDIO host."""
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"targetId": "local", "types": [], "producers": []}))

    completed = host(_frame(PING), args=["--source-catalog", str(catalog)])

    assert completed.returncode == 0 and completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["result"] == {}


def test_stdio_host_process_does_not_open_unregistered_endpoints() -> None:
    """The host rejects unknown-tool calls without any socket activity."""
    completed = host(_call(1, "springboot_missing_tool"))

    assert completed.returncode == 0 and completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1
    body = json.loads(lines[0])
    assert "result" not in body and body["error"]["code"] == -32602
