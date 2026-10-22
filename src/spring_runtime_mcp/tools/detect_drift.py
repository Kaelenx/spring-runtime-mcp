"""Compare caller-supplied expectations with a read-only runtime snapshot."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..adapters.drift import compare_drift
from ..contracts.definitions import validate_arguments
from ..models.common import NormalizationError
from ..models.queries import DriftQuery
from ..transport.response_gate import EndpointReadError
from ..services import Services
from ..diagnostics import error, failed, partial, safe_code
from .support import tool_handler, finalize, read_checked, resume, shell, stamp

_VERSION = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")
_STARTUP_UNAVAILABLE = frozenset(
    {"CAPABILITY_DISABLED", "ENDPOINT_NOT_EXPOSED", "STARTUP_DATA_UNAVAILABLE"}
)
_STARTUP_EVIDENCE_GAP = "STARTUP_EVIDENCE_UNAVAILABLE"
_DIFF_FIELDS = (
    "id",
    "dimension",
    "identity",
    "contextId",
    "expected",
    "actual",
    "evidenceLevel",
    "affectedDependencyIds",
)


def _profile_evidence(body: Any, services: Services) -> Any:
    """Profiles permission does not grant unregistered property evidence."""
    if not isinstance(body, dict) or services.config_policy is None:
        return None
    sources = []
    for source in (
        body.get("propertySources", [])
        if isinstance(body.get("propertySources"), list)
        else []
    ):
        if not isinstance(source, dict) or not isinstance(
            source.get("properties"), dict
        ):
            continue
        sources.append(
            {
                "name": source.get("name"),
                "properties": {
                    key: value
                    for key, value in source["properties"].items()
                    if services.config_policy.allows(key)
                },
            }
        )
    return {"activeProfiles": body.get("activeProfiles"), "propertySources": sources}


def _project(page: dict[str, Any]) -> dict[str, Any]:
    data = page.get("data")
    if not isinstance(data, dict) or "items" not in data:
        return page
    missing: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    for item in data.pop("items") or []:
        if not isinstance(item, dict):
            continue
        record = {field: item.get(field) for field in _DIFF_FIELDS}
        if not isinstance(record["affectedDependencyIds"], list):
            record["affectedDependencyIds"] = []
        if str(record.get("id") or "").startswith("u"):
            unexpected.append(record)
        else:
            missing.append(record)
    data["missingInRuntime"] = missing
    data["unexpectedInRuntime"] = unexpected
    data.setdefault("profileMismatch", None)
    data.setdefault("rootCauseInferences", [])
    return page


def _startup_start(body: Any) -> str | None:
    if not isinstance(body, dict) or not isinstance(body.get("timeline"), dict):
        return None
    start = body["timeline"].get("startTime")
    return start if isinstance(start, str) else None


def _later(modified: str, started: str) -> bool:
    try:
        left = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        right = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return False
    return left.tzinfo is not None and right.tzinfo is not None and left > right


def _inferences(
    missing: list[dict[str, Any]],
    unexpected: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    *,
    environment_override: bool,
    not_restarted: bool,
) -> list[dict[str, Any]]:
    evidence = [row["id"] for row in (*missing, *unexpected)][:8]
    drifted = bool(
        missing or unexpected or (profile and profile.get("state") == "mismatch")
    )
    rows: list[dict[str, Any]] = []
    if profile and profile.get("state") == "mismatch":
        rows.append(
            {"code": "PROFILE_MISMATCH", "confidence": "supported", "evidenceIds": []}
        )
        if environment_override:
            rows.append(
                {
                    "code": "ENVIRONMENT_OVERRIDE",
                    "confidence": "supported",
                    "evidenceIds": [],
                }
            )
    if not_restarted and drifted:
        rows.append(
            {
                "code": "POSSIBLE_NOT_RESTARTED",
                "confidence": "candidate",
                "evidenceIds": list(evidence),
            }
        )
    if missing or unexpected:
        rows.append(
            {
                "code": "INSUFFICIENT_EVIDENCE",
                "confidence": "unknown",
                "evidenceIds": list(evidence),
            }
        )
    return rows[:4]


def _assign(rows: list[dict[str, Any]], prefix: str) -> None:
    for index, row in enumerate(rows, start=1):
        row["id"] = f"{prefix}{index:06d}"


async def _optional_startup(
    services: Services, query: DriftQuery, snapshot: str, result: dict[str, Any]
) -> tuple[Any | None, list[dict[str, Any]], list[str]]:
    if query.source_modified_at is None:
        return None, [], []
    try:
        observation = await read_checked(services, query.target_id, "startup", snapshot)
    except NormalizationError as exc:
        if safe_code(exc.code) in _STARTUP_UNAVAILABLE:
            # The caller explicitly supplied sourceModifiedAt, so startup timing
            # is required evidence. Missing it is a gap, never a silent success.
            return None, [], [_STARTUP_EVIDENCE_GAP]
        return (
            None,
            [
                error(
                    exc.code,
                    "startup",
                    exc.http_status if isinstance(exc, EndpointReadError) else None,
                )
            ],
            [],
        )
    stamp(result, observation, "startup", snapshot)
    version = (
        observation.body.get("springBootVersion")
        if isinstance(observation.body, dict)
        else None
    )
    if isinstance(version, str) and _VERSION.fullmatch(version):
        result["source"]["bootVersion"] = version
    return observation.body, [], []


@tool_handler
async def detect_drift(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    request_target = "local"
    try:
        query = validate_arguments(arguments, "springboot_detect_drift")
        request_target = query.target_id
        assert isinstance(query, DriftQuery)
        # A caller revision is not an observed build id. Do not infer
        # POSSIBLE_WRONG_INSTANCE unless a future authorized info read proves it.
        if query.source_revision is not None and not isinstance(
            query.source_revision, str
        ):
            raise NormalizationError("INVALID_ARGUMENT")
        if query.cursor:
            return _project(resume(services, query))
        snapshot = uuid4().hex
        if (
            query.target_id not in services.targets
            or services.catalog.target_id != query.target_id
        ):
            raise NormalizationError("TARGET_NOT_REGISTERED")
        result = shell(query.target_id, "registered-target-drift", "drift-1")
        plan: list[str] = []
        if query.expected_beans is not None:
            plan.append("beans")
        if query.expected_routes is not None:
            plan.append("mappings")
        if query.expected_migration is not None:
            plan.append(query.expected_migration.engine)
        if query.expected_profiles is not None:
            plan.append("env")
        bodies: dict[str, Any] = {}
        failures: list[dict[str, Any]] = []
        for endpoint in plan:
            try:
                if endpoint == "env" and (
                    services.config_policy is None
                    or not services.config_policy.profiles
                ):
                    raise EndpointReadError("CAPABILITY_DISABLED", "env")
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
            bodies[endpoint] = observation
            stamp(result, observation, endpoint, snapshot)
        if plan and not bodies and failures:
            first = failures[0]
            return failed(
                first["code"],
                query.target_id,
                endpoint=first["endpointId"],
                http_status=first["httpStatus"],
            )
        startup_body, startup_errors, startup_gaps = await _optional_startup(
            services, query, snapshot, result
        )
        failures.extend(startup_errors)
        compared = compare_drift(
            query,
            beans=bodies.get("beans"),
            mappings_body=None if "mappings" not in bodies else bodies["mappings"].body,
            flyway_body=None if "flyway" not in bodies else bodies["flyway"].body,
            migration_observed="liquibase" in bodies,
            env_body=_profile_evidence(bodies["env"].body, services)
            if "env" in bodies
            else None,
            catalog=services.catalog,
        )
        missing = list(compared["missing"])
        unexpected = list(compared["unexpected"])
        _assign(missing, "m")
        _assign(unexpected, "u")
        started = _startup_start(startup_body)
        not_restarted = bool(
            query.source_modified_at
            and started
            and _later(query.source_modified_at, started)
        )
        if query.source_modified_at and startup_body is not None and started is None:
            compared["gaps"] = [*compared["gaps"], "STARTUP_TIMING_UNOBSERVED"]
            compared["incomplete"] = True
        profile = compared["profile"]
        result["data"] = {
            "items": [*missing, *unexpected],
            "profileMismatch": profile,
            "rootCauseInferences": _inferences(
                missing,
                unexpected,
                profile,
                environment_override=bool(compared["environment_override"]),
                not_restarted=not_restarted,
            ),
        }
        result["errors"] = failures[:8]
        gaps = (
            list(compared["gaps"])
            + [safe_code(item["code"]) for item in failures]
            + startup_gaps
        )
        if startup_gaps:
            compared["incomplete"] = True
        if compared["incomplete"] and not gaps:
            gaps.append("EVIDENCE_INCOMPLETE")
        result["totalMatched"] = (
            None
            if compared["incomplete"] or failures or startup_gaps
            else len(missing)
            + len(unexpected)
            + (
                1
                if isinstance(profile, dict) and profile.get("state") == "mismatch"
                else 0
            )
        )
        partial(result, gaps)
        return _project(finalize(result, services, query))
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
