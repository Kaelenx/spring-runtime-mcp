"""Explicit real-Shop acceptance using a disposable database and existing JWT auth.

This is never imported by default tests. Build the approved Maven profile first;
only bounded, sanitized tool summaries are saved, never upstream response bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp import Client, StdioServerParameters

from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.policy.output_guard import ensure_safe_output
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.transport.responses import serialize_response

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/tdd-runtime"


def encoded(value: object) -> bytes:
    data = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def credential(secret: str) -> str:
    now = int(time.time())
    unsigned = (
        encoded({"alg": "HS256", "typ": "JWT"})
        + b"."
        + encoded(
            {
                "iss": "shop",
                "sub": "mcp_review",
                "uid": 1,
                "iat": now,
                "exp": now + 600,
                "jti": secrets.token_hex(16),
            }
        )
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), unsigned, hashlib.sha256).digest()
    ).rstrip(b"=")
    return (unsigned + b"." + signature).decode()


def safe_environment() -> dict[str, str]:
    # Existing app/DB/JVM overrides must not redirect this disposable launch.
    blocked = ("SPRING_", "AUTH_", "APP_", "MANAGEMENT_", "LOGGING_")
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(blocked)
        and key not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}
    }


def ready(
    process: subprocess.Popen[bytes], phase: list[str], timeout: float = 90
) -> int:
    events: queue.Queue[int] = queue.Queue(maxsize=1)

    def drain() -> None:
        assert process.stdout is not None
        # Discard all framework logs; do not retain or print a target traceback.
        while line := process.stdout.readline(8192):
            match = re.fullmatch(rb"MCP_DIAGNOSTICS_READY=(\d+)\s*", line)
            if match and events.empty():
                events.put(int(match[1]))
            failed = re.fullmatch(rb"MCP_DIAGNOSTICS_FAILED=([A-Za-z0-9_.$]+)\s*", line)
            if failed:
                phase[0] = "startup-" + failed[1].decode()
            missing = re.fullmatch(
                rb"MCP_DIAGNOSTICS_MISSING=([A-Za-z0-9_.$]+)\s*", line
            )
            if missing:
                phase[0] = "missing-bean-" + missing[1].decode()

    threading.Thread(target=drain, daemon=True).start()
    deadline = time.monotonic() + timeout
    while process.poll() is None and time.monotonic() < deadline:
        try:
            return events.get(timeout=0.2)
        except queue.Empty:
            continue
    raise RuntimeError("TARGET_STARTUP_FAILED")


def calls() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("springboot_inspect_beans", {"beanNamePattern": "authService"}),
        ("springboot_inspect_beans", {"beanNamePattern": "missingReviewBean"}),
        (
            "springboot_verify_mappings",
            {"pathPattern": "/api/auth/login", "httpMethod": "POST"},
        ),
        ("springboot_diagnose_health", {"componentPath": ["db"]}),
        ("springboot_check_migrations", {"engine": "flyway"}),
        ("springboot_analyze_startup", {"minDurationMs": 10}),
        (
            "springboot_detect_drift",
            {
                "expectedBeans": [{"name": "authService"}],
                "expectedRoutes": [{"path": "/api/auth/login", "httpMethod": "POST"}],
                "expectedMigrationVersion": {"engine": "flyway", "version": "1"},
            },
        ),
    ]


async def verify(port: int, token: str, phase: list[str]) -> dict[str, Any]:
    target = f"http://127.0.0.1:{port}"
    env = safe_environment()
    env.update(PYTHONPATH=str(ROOT / "src"), TDD_REVIEW_BEARER=token)
    report: dict[str, Any] = {
        "verifiedAt": datetime.now(UTC).isoformat(),
        "bootVersion": None,
        "java": 21,
        "webStack": "mvc",
        "authentication": "Bearer",
        "database": "disposable-postgresql-16",
        "target": "registered-loopback",
        "tools": [],
    }
    pipeline = SanitizerPipeline((token,))
    phase[0] = "anonymous-authorization"
    async with Client(
        StdioServerParameters(
            command=sys.executable,
            args=["-m", "spring_runtime_mcp.cli", "--base-url", target],
            env=env,
        ),
        mode="legacy",
        read_timeout_seconds=20,
    ) as anonymous:
        rejected = await anonymous.call_tool(
            "springboot_inspect_beans", {"beanNamePattern": "authService"}
        )
        envelope = rejected.structured_content
        assert isinstance(envelope, dict) and envelope["status"] == "denied"
        assert envelope["errors"][0]["httpStatus"] in (401, 403)
        report["anonymousAccess"] = "denied"
    phase[0] = "authenticated-discovery"
    async with Client(
        StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "spring_runtime_mcp.cli",
                "--base-url",
                target,
                "--token-env",
                "TDD_REVIEW_BEARER",
            ],
            env=env,
        ),
        mode="legacy",
        read_timeout_seconds=20,
    ) as client:
        assert len((await client.list_tools()).tools) == 8
        for index, (tool, arguments) in enumerate(calls(), start=1):
            phase[0] = tool + "-" + str(index)
            response = await client.call_tool(tool, arguments)
            result = response.structured_content
            assert isinstance(result, dict)
            Draft202012Validator(definition(tool)["outputSchema"]).validate(result)
            ensure_safe_output(result, sanitizer=pipeline)
            serialize_response(result, index, sanitizer=pipeline)
            assert result["status"] in ("ok", "partial"), (tool, "TOOL_FAILED")
            data = result["data"]
            if index == 1:
                assert data["items"] and data["items"][0]["name"] == "authService"
            if tool == "springboot_verify_mappings":
                assert data["items"] and "/api/auth/login" in data["items"][0]["paths"]
            if tool == "springboot_diagnose_health":
                assert result["status"] == "partial" and result["totalMatched"] is None
            if tool == "springboot_check_migrations":
                assert any(
                    row["version"] == "1" and row["state"] == "APPLIED"
                    for row in data["items"]
                )
            if tool == "springboot_analyze_startup":
                report["bootVersion"] = data["springBootVersion"]
                assert data["items"]
            if tool == "springboot_detect_drift":
                assert data["missingInRuntime"] == []
            report["tools"].append(
                {
                    "tool": tool,
                    "status": result["status"],
                    "totalMatched": result["totalMatched"],
                    "coverage": result["coverage"]["state"],
                    "gaps": result["coverage"]["gaps"],
                    "truncated": result["truncated"],
                }
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--classpath-file", type=Path, required=True)
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path, default=FIXTURE / "verification.json")
    args = parser.parse_args()
    if args.source_revision and not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        parser.error("source revision must be a full Git commit ID")
    classpath = os.pathsep.join(
        [
            str(args.project / "target/classes"),
            str(args.project / "target/test-classes"),
            args.classpath_file.read_text().strip(),
        ]
    )
    secret = secrets.token_urlsafe(24)
    token = credential(secret)
    env = safe_environment()
    env["AUTH_JWT_SECRET"] = secret
    process = subprocess.Popen(
        [
            "java",
            "-cp",
            classpath,
            str(FIXTURE / "DiagnosticsLauncher.java"),
            str(FIXTURE / "application-runtime-diagnostics.properties"),
        ],
        cwd=args.project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    phase = ["startup"]
    try:
        port = ready(process, phase)
        report = asyncio.run(verify(port, token, phase))
        if args.source_revision:
            report["sourceRevision"] = args.source_revision
    except Exception:
        print(
            f"Real Shop acceptance failed at {phase[0]}; no target response, credentials or stack were exported.",
            file=sys.stderr,
        )
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    report["targetProcessStopped"] = process.poll() is not None
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Real Shop acceptance passed; sanitized summary saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
