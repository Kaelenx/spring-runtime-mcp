"""Read-only health handler for Boot 2.x details and 3.x/4.x components."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..adapters.health import normalize_health
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import HealthQuery
from ..transport.response_gate import EndpointReadError
from .inspect_beans import Services, failed, partial
from .support import finalize, read_checked, resume, shell, stamp

_ENDPOINTS = {
    "application": "health",
    "liveness": "health-liveness",
    "readiness": "health-readiness",
}


async def diagnose_health(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    try:
        query = validate_arguments(arguments, "springboot_diagnose_health")
        assert isinstance(query, HealthQuery)
        if query.cursor:
            return resume(services, query)
        endpoint = _ENDPOINTS[query.group]
        snapshot = uuid4().hex
        observation = await read_checked(services, query.target_id, endpoint, snapshot)
        overall, items, gaps = normalize_health(
            observation.body,
            component_path=query.component_path,
            include_details=query.include_details,
            include_healthy=query.include_healthy,
            max_depth=query.max_depth,
        )
        result = shell(query.target_id, "registered target health", "health-1")
        stamp(result, observation, endpoint, snapshot)
        for item in items:
            item["endpointId"] = endpoint
            item["observationId"] = observation.observation_id
        result["data"] = {
            "overallStatus": overall,
            "activeGroup": query.group,
            "items": items,
        }
        result["totalMatched"] = len(items)
        partial(result, gaps)
        if "HEALTH_DETAILS_NOT_VISIBLE" in result["coverage"]["gaps"]:
            result["nextAction"] = {
                "kind": "remediate",
                "reasonCode": "HEALTH_DETAILS_NOT_VISIBLE",
                "filterSuggestions": [],
            }
        return finalize(result, services, query)
    except NormalizationError as exc:
        return failed(
            exc.code,
            endpoint=exc.endpoint_id if isinstance(exc, EndpointReadError) else None,
            http_status=exc.http_status if isinstance(exc, EndpointReadError) else None,
        )
    except TimeoutError:
        return failed("TARGET_TIMEOUT")
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        return failed("MALFORMED_EVIDENCE")
    except Exception:
        return failed("INTERNAL_ERROR")
