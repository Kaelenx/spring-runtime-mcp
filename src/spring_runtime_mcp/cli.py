"""Operator entrypoint. Tool arguments cannot select the actuator origin."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from .adapters.source_catalog import load_source_catalog
from .models.common import AnalysisBudget
from .server import build_services, run_stdio
from .transport.bounded_json import loads_bounded

SOURCE_CATALOG_MAX_BYTES = 262144


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
    parser.add_argument(
        "--source-catalog",
        default=os.environ.get("SPRING_RUNTIME_SOURCE_CATALOG"),
        help="Operator-registered JSON workspace facts for this target.",
    )
    args = parser.parse_args(argv)
    try:
        timeout = float(args.timeout)
        catalog = None
        if args.source_catalog:
            catalog = load_source_catalog(
                loads_bounded(
                    Path(args.source_catalog).read_bytes(),
                    max_bytes=SOURCE_CATALOG_MAX_BYTES,
                ),
                target_id=args.target_id,
                budget=AnalysisBudget(),
            )
        services = build_services(
            args.base_url, args.target_id, timeout, source_catalog=catalog
        )
    except (TypeError, ValueError, OSError, UnicodeError):
        sys.stderr.write("invalid server configuration\n")
        raise SystemExit(2) from None
    try:
        asyncio.run(run_stdio(services))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
