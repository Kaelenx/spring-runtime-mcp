"""Operator entrypoint. Tool arguments cannot select the actuator origin."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from .adapters.source_catalog import load_source_catalog
from .models.common import AnalysisBudget
from .policy.configuration import ConfigReadPolicy
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
    parser.add_argument(
        "--token-env",
        help="Name of the environment variable holding this target's Bearer token.",
    )
    parser.add_argument(
        "--config-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help="Register one property prefix for opt-in config diagnostics.",
    )
    parser.add_argument(
        "--config-property",
        action="append",
        default=[],
        metavar="KEY",
        help="Register one property key for opt-in config diagnostics.",
    )
    parser.add_argument(
        "--config-profiles",
        action="store_true",
        help="Allow reading active profiles for drift comparison.",
    )
    parser.add_argument(
        "--config-allow-values",
        action="store_true",
        help="Allow registered config values after sanitization (off by default).",
    )
    args = parser.parse_args(argv)
    try:
        timeout = float(args.timeout)
        bearer_token = None
        if args.token_env is not None:
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.token_env) is None:
                raise ValueError("INVALID_CREDENTIAL_REGISTRATION")
            bearer_token = os.environ.get(args.token_env)
            if not bearer_token:
                raise ValueError("INVALID_CREDENTIAL_REGISTRATION")
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
        config_policy = None
        if (
            args.config_prefix
            or args.config_property
            or args.config_profiles
            or args.config_allow_values
        ):
            if not (args.config_prefix or args.config_property or args.config_profiles):
                raise ValueError("INVALID_CONFIG_REGISTRATION")
            config_policy = ConfigReadPolicy(
                properties=frozenset(args.config_property),
                prefixes=frozenset(args.config_prefix),
                allow_values=args.config_allow_values,
                profiles=args.config_profiles,
            )
        services = build_services(
            args.base_url,
            args.target_id,
            timeout,
            source_catalog=catalog,
            config_policy=config_policy,
            bearer_token=bearer_token,
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
