"""Operator entrypoint. Tool arguments cannot select the actuator origin."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .server import build_services, run_stdio


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="spring-runtime-mcp")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("SPRING_RUNTIME_BASE_URL", "http://127.0.0.1:18081"),
    )
    parser.add_argument(
        "--target-id",
        default=os.environ.get("SPRING_RUNTIME_TARGET_ID", "local"),
    )
    parser.add_argument(
        "--timeout",
        default=os.environ.get("SPRING_RUNTIME_TIMEOUT", "5"),
    )
    args = parser.parse_args(argv)
    try:
        timeout = float(args.timeout)
        services = build_services(args.base_url, args.target_id, timeout)
    except (TypeError, ValueError):
        sys.stderr.write("invalid server configuration\n")
        raise SystemExit(2) from None
    try:
        asyncio.run(run_stdio(services))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
