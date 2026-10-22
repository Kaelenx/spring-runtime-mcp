"""Drive spring-runtime-mcp against examples/spring-boot-demo and save a transcript.

Start the demo app first (see examples/spring-boot-demo/README.md). This script
only talks to the loopback management port the operator configured; it opens no
other connection.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def requests() -> list[tuple[str, dict[str, object]]]:
    return [
        (
            "initialize",
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "demo-transcript", "version": "0"},
                },
            },
        ),
        ("initialized", {"jsonrpc": "2.0", "method": "notifications/initialized"}),
        ("tools/list", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        (
            "inspect_beans",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "springboot_inspect_beans",
                    "arguments": {
                        "beanNamePattern": "paymentGateway",
                        "contextId": "demo",
                        "conditionDepth": 4,
                    },
                },
            },
        ),
        (
            "verify_mappings",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "springboot_verify_mappings",
                    "arguments": {
                        "pathPattern": "/orders/{id}",
                        "httpMethod": "GET",
                        "contextId": "demo",
                    },
                },
            },
        ),
        (
            "diagnose_health",
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "springboot_diagnose_health", "arguments": {}},
            },
        ),
        (
            "check_migrations",
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "springboot_check_migrations",
                    "arguments": {"engine": "flyway"},
                },
            },
        ),
        (
            "analyze_startup",
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "springboot_analyze_startup",
                    "arguments": {"minDurationMs": 50},
                },
            },
        ),
        (
            "detect_drift",
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "springboot_detect_drift",
                    "arguments": {
                        "expectedBeans": [
                            {"name": "paymentGateway", "contextId": "demo"},
                            {"name": "orderController", "contextId": "demo"},
                        ],
                        "expectedScopeComplete": True,
                        "expectedRoutes": [
                            {"path": "/orders/{id}", "httpMethod": "GET"}
                        ],
                        "expectedMigrationVersion": {
                            "engine": "flyway",
                            "version": "1",
                        },
                    },
                },
            },
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument(
        "--source-catalog",
        type=Path,
        default=ROOT / "examples/spring-boot-demo/source-catalog.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples/spring-boot-demo/transcript.jsonl",
    )
    args = parser.parse_args()
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "spring_runtime_mcp.cli",
            "--base-url",
            args.base_url,
            "--source-catalog",
            str(args.source_catalog),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    transcript = []
    try:
        for name, payload in requests():
            process.stdin.write(json.dumps(payload).encode() + b"\n")
            process.stdin.flush()
            if "id" not in payload:
                continue
            line = process.stdout.readline()
            if not line:
                raise SystemExit(f"no response for {name}")
            transcript.append({"case": name, "response": json.loads(line)})
        process.stdin.close()
        returncode = process.wait(timeout=60)
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in transcript)
    )
    print(f"wrote {args.output} ({len(transcript)} responses), rc={returncode}")
    if stderr:
        print(stderr.decode(errors="replace")[:500])
    return 0 if returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
