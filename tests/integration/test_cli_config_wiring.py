"""The public CLI must be able to enable the opt-in config capability."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _frame(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _read(process: subprocess.Popen[bytes], timeout: float = 10) -> dict[str, Any]:
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    assert ready, "CLI did not answer before the deadline"
    line = process.stdout.readline()
    assert line
    return json.loads(line)


def _config_call(extra_args: list[str]) -> tuple[dict[str, Any], bytes]:
    environment = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "spring_runtime_mcp.cli",
            *extra_args,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        cwd=ROOT,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "config-wiring", "version": "0"},
                    },
                }
            )
        )
        process.stdin.flush()
        _read(process)
        process.stdin.write(
            _frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        process.stdin.write(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "springboot_inspect_config",
                        "arguments": {"prefix": "spring.application"},
                    },
                }
            )
        )
        process.stdin.flush()
        response = _read(process)
        process.stdin.close()
        process.wait(timeout=10)
        stderr = process.stderr.read() if process.stderr else b""
        return response, stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_cli_config_prefix_opens_the_capability_without_a_target() -> None:
    response, stderr = _config_call(["--config-prefix", "spring.application"])
    result = response["result"]
    envelope = result["structuredContent"]
    codes = [item["code"] for item in envelope["errors"]]
    assert result["isError"] is True
    assert codes == ["TARGET_CONNECTION_REFUSED"], envelope
    assert "CAPABILITY_DISABLED" not in codes
    assert b"Traceback" not in stderr


def test_config_value_permission_without_a_selector_is_rejected() -> None:
    environment = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "spring_runtime_mcp.cli",
            "--config-allow-values",
        ],
        input=b"",
        capture_output=True,
        timeout=10,
        env=environment,
        cwd=ROOT,
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"invalid server configuration" in completed.stderr
