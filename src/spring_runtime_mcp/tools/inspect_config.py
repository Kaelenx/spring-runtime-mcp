"""Read-only env and configuration-properties handler."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..adapters.config import normalize_config
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import ConfigQuery
from ..transport.response_gate import EndpointReadError
from .inspect_beans import Services, error, failed, partial, safe_code
from .support import finalize, read_checked, resume, shell, stamp


async def inspect_config(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    try:
        query = validate_arguments(arguments, "springboot_inspect_config")
        assert isinstance(query, ConfigQuery)
        if query.cursor:
            return resume(services, query)
        if (
            query.target_id not in services.targets
            or services.catalog.target_id != query.target_id
        ):
            raise NormalizationError("TARGET_NOT_REGISTERED")
        snapshot = uuid4().hex
        result = shell(query.target_id, "registered target config", "config-1")
        bodies: dict[str, Any] = {}
        observation_ids: dict[str, str] = {}
        failures: list[dict[str, Any]] = []
        for endpoint in ("env", "configprops"):
            try:
                observation = await read_checked(
                    services, query.target_id, endpoint, snapshot
                )
            except NormalizationError as exc:
                failures.append(
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
        if not bodies:
            first = failures[0]
            return failed(
                first["code"],
                query.target_id,
                endpoint=first["endpointId"],
                http_status=first["httpStatus"],
            )
        profiles, items, gaps = normalize_config(
            bodies.get("env"), bodies.get("configprops"), query, services.sanitizer
        )
        for item in items:
            item["observationId"] = observation_ids.get(
                item["endpointId"], "observation_config"
            )
        result["data"] = {"activeProfiles": profiles, "items": items}
        result["totalMatched"] = len(items)
        result["errors"] = failures
        partial(result, [safe_code(item["code"]) for item in failures] + gaps)
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
