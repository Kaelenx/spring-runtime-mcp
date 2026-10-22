"""Read-only Flyway and Liquibase handler. It never executes migrations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..adapters.migrations import normalize_migrations
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import MigrationQuery
from ..transport.response_gate import EndpointReadError
from ..services import Services
from ..diagnostics import error, failed, partial, safe_code
from .support import tool_handler, finalize, read_checked, resume, shell, stamp


@tool_handler
async def check_migrations(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    request_target = "local"
    try:
        query = validate_arguments(arguments, "springboot_check_migrations")
        request_target = query.target_id
        assert isinstance(query, MigrationQuery)
        if query.cursor:
            return resume(services, query)
        snapshot = uuid4().hex
        if (
            query.target_id not in services.targets
            or services.catalog.target_id != query.target_id
        ):
            raise NormalizationError("TARGET_NOT_REGISTERED")
        engines = ("flyway", "liquibase") if query.engine == "auto" else (query.engine,)
        bodies: dict[str, Any] = {}
        observation_ids: dict[str, str] = {}
        result = shell(query.target_id, "registered target migrations", "migrations-1")
        gaps: list[str] = []
        for endpoint in engines:
            try:
                observation = await read_checked(
                    services, query.target_id, endpoint, snapshot
                )
            except NormalizationError as exc:
                gaps.append(safe_code(exc.code))
                result["errors"].append(
                    error(
                        exc.code,
                        endpoint,
                        exc.http_status if isinstance(exc, EndpointReadError) else None,
                    )
                )
                continue
            bodies[endpoint] = observation.body
            observation_ids[endpoint] = observation.observation_id
            stamp(result, observation, endpoint, snapshot)
        if not bodies and result["errors"]:
            return failed(
                result["errors"][0]["code"],
                query.target_id,
                endpoint=result["errors"][0]["endpointId"],
                http_status=result["errors"][0]["httpStatus"],
            )
        items, parsed_gaps, visibility = normalize_migrations(
            bodies.get("flyway"), bodies.get("liquibase"), query
        )
        gaps.extend(parsed_gaps)
        invalid = [
            engine
            for engine in bodies
            if engine.upper() + "_EVIDENCE_INVALID" in parsed_gaps
        ]
        if invalid and len(invalid) == len(bodies):
            return failed("MALFORMED_EVIDENCE", query.target_id, endpoint=invalid[0])
        for engine in invalid:
            result["errors"].append(error("MALFORMED_EVIDENCE", engine))
        for item in items:
            item["observationId"] = observation_ids.get(
                item["endpointId"], "observation_baseline"
            )
        result["data"] = {"pendingVisibility": visibility, "items": items}
        result["totalMatched"] = None if result["errors"] or gaps else len(items)
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
