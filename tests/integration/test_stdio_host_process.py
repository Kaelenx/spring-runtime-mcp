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
    """TC-BUG-12: array/object/null names stay controlled through the real host."""
    stdin = b"".join(
        [
            _call(1, []),
            _call(2, {}),
            _call(3, None),
            _call(4, True),
            _call(5, "springboot_missing_tool"),
            b"not-json\n",
            _frame(PING),
        ]
    )

    completed = host(stdin)

    assert completed.returncode == 0 and completed.stderr == b""
    lines = completed.stdout.splitlines()
    assert len(lines) == 7, completed.stdout
    codes = []
    for line in lines[:6]:
        body = json.loads(line)
        if "result" in body:
            codes.append(body["result"]["structuredContent"]["errors"][0]["code"])
        else:
            codes.append(body["error"]["code"])
    assert codes == [
        "INVALID_ARGUMENT",
        "INVALID_ARGUMENT",
        "INVALID_ARGUMENT",
        "INVALID_ARGUMENT",
        -32602,
        -32700,
    ], codes
    assert b"INTERNAL_ERROR" not in completed.stdout
    assert b"Traceback" not in completed.stderr
    assert json.loads(lines[6])["result"] == {}


def test_stdio_host_process_bounds_openapi_fanout_over_the_wire(
    openapi_fixture: Any,
) -> None:
    """TC-BUG-01: a 50-way/eight-level OpenAPI fan-out stays bounded on the wire."""
    base_url, requests = openapi_fixture

    completed = host(
        _call(
            1,
            "springboot_search_openapi",
            {"schemaName": "S0", "maxRefDepth": 8},
        ),
        base_url=base_url,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert b"Traceback" not in completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == 1 and len(lines[0]) <= 16_384, (
        f"wire frame must stay bounded, got {len(completed.stdout)} bytes"
    )
    response = json.loads(lines[0])
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
