"""Read-only local OpenAPI search. Remote $ref values are not resolved."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..adapters.openapi import normalize_openapi
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import OpenApiQuery
from ..transport.response_gate import EndpointReadError
from .inspect_beans import Services, failed, partial
from .support import finalize, read_checked, resume, shell, stamp


async def search_openapi(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    request_target = "local"
    try:
        query = validate_arguments(arguments, "springboot_search_openapi")
        request_target = query.target_id
        assert isinstance(query, OpenApiQuery)
        if query.cursor:
            return resume(services, query)
        snapshot = uuid4().hex
        observation = await read_checked(services, query.target_id, "openapi", snapshot)
        version, candidate, items, gaps, blocked = normalize_openapi(
            observation.body, query
        )
        result = shell(query.target_id, "registered target openapi", "openapi-1")
        stamp(result, observation, "openapi", snapshot)
        for item in items:
            item["endpointId"] = "openapi"
            item["observationId"] = observation.observation_id
        result["data"] = {
            "openapiVersion": version,
            "candidateId": candidate,
            "items": items,
        }
        result["totalMatched"] = len(items)
        partial(result, gaps)
        limit_codes = ("REF_DEPTH_LIMIT", "REF_CYCLE", "REF_EXPANSION_LIMIT")
        if any(code in gaps for code in limit_codes):
            reason = next(code for code in limit_codes if code in gaps)
            result["truncated"] = True
            result["truncationReasons"] = [code for code in limit_codes if code in gaps]
            result["nextAction"] = {
                "kind": "refine",
                "reasonCode": reason,
                "filterSuggestions": (
                    [{"parameter": "schemaName", "value": blocked}] if blocked else []
                ),
            }
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
