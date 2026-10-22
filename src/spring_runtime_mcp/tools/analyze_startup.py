"""Read-only startup timeline handler."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..adapters.startup import normalize_startup
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import StartupQuery
from ..transport.response_gate import EndpointReadError
from ..services import Services
from ..diagnostics import failed, partial
from .support import tool_handler, finalize, read_checked, resume, shell, stamp


@tool_handler
async def analyze_startup(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    request_target = "local"
    try:
        query = validate_arguments(arguments, "springboot_analyze_startup")
        request_target = query.target_id
        assert isinstance(query, StartupQuery)
        if query.cursor:
            return resume(services, query)
        snapshot = uuid4().hex
        observation = await read_checked(services, query.target_id, "startup", snapshot)
        version, total, basis, complete, items, gaps = normalize_startup(
            observation.body,
            step_name_pattern=query.step_name_pattern,
            bean_name_pattern=query.bean_name_pattern,
            min_duration_ms=query.min_duration_ms,
            max_depth=query.max_depth,
        )
        result = shell(query.target_id, "registered target startup", "startup-1")
        stamp(result, observation, "startup", snapshot)
        for index, item in enumerate(items, start=1):
            item["id"] = f"startup_{index:06d}"
            item["endpointId"] = "startup"
            item["observationId"] = observation.observation_id
        result["data"] = {
            "springBootVersion": version,
            "totalDurationMs": total,
            "durationBasis": basis,
            "timelineComplete": complete,
            "items": items,
        }
        limited = {
            "STARTUP_NODE_LIMIT",
            "STARTUP_TAG_LIMIT",
            "STARTUP_DEPTH_LIMIT",
        } & set(gaps)
        result["totalMatched"] = None if limited else len(items)
        if limited:
            result["truncated"] = True
            result["truncationReasons"] = sorted(
                set(result["truncationReasons"] + list(limited))
            )[:8]
        if not complete:
            gaps = [*gaps, "STARTUP_TIMELINE_INCOMPLETE"]
        partial(result, gaps)
        return finalize(result, services, query)
    except NormalizationError as exc:
        return failed(
            exc.code,
            request_target,
            endpoint=exc.endpoint_id if isinstance(exc, EndpointReadError) else None,
            http_status=exc.http_status if isinstance(exc, EndpointReadError) else None,
        )
    except TimeoutError:
        return failed("TARGET_TIMEOUT", request_target)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        return failed("MALFORMED_EVIDENCE", request_target)
    except Exception:
        return failed("INTERNAL_ERROR", request_target)
