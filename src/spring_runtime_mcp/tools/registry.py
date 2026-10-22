"""Implemented tool handlers. Missing tools stay explicit failures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..contracts.definitions import list_tools
from .analyze_startup import analyze_startup
from .check_migrations import check_migrations
from .detect_drift import detect_drift
from .diagnose_health import diagnose_health
from ..services import Services
from .inspect_beans import inspect_beans
from ..diagnostics import failed
from .inspect_config import inspect_config
from .search_openapi import search_openapi
from .verify_mappings import verify_mappings

Handler = Callable[[Mapping[str, Any], Services], Awaitable[dict[str, Any]]]

HANDLERS: dict[str, Handler] = {
    "springboot_analyze_startup": analyze_startup,
    "springboot_check_migrations": check_migrations,
    "springboot_detect_drift": detect_drift,
    "springboot_diagnose_health": diagnose_health,
    "springboot_inspect_beans": inspect_beans,
    "springboot_inspect_config": inspect_config,
    "springboot_search_openapi": search_openapi,
    "springboot_verify_mappings": verify_mappings,
}


async def call_tool(
    name: str, arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        known = {item["name"] for item in list_tools()}
        return failed("CAPABILITY_DISABLED" if name in known else "UNKNOWN_TOOL")
    return await handler(arguments, services)
