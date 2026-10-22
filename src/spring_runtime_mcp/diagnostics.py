"""Shared safe diagnostic envelopes and observation checks, independent of tools."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .models.common import NormalizationError, ParsedObservation
from .policy.output_guard import (
    DIAGNOSTIC_MESSAGE,
    REVIEW_AUTHORIZATION,
    REVIEW_STARTUP,
    REVIEW_TARGET,
)
from .transport.bounded_json import validate_tree
from .transport.response_gate import HEALTH_ENDPOINTS

SAFE_CODES = frozenset(
    [
        "INVALID_ARGUMENT",
        "TARGET_NOT_REGISTERED",
        "OBSERVATION_IDENTITY_MISMATCH",
        "ENDPOINT_UNAUTHORIZED",
        "ENDPOINT_UNAVAILABLE",
        "INVALID_OBSERVATION_TIME",
        "CONTEXT_HIERARCHY_CONFLICT",
        "CAPABILITY_DISABLED",
        "CURSOR_EXPIRED",
        "CURSOR_QUERY_MISMATCH",
        "SNAPSHOT_TOO_LARGE",
        "INVALID_RESPONSE",
        "RESPONSE_TOO_LARGE",
        "TARGET_TIMEOUT",
        "GRAPH_LIMIT_REACHED",
        "NORMALIZATION_LIMIT",
        "NON_TREE_JSON",
        "INVALID_JSON",
        "INVALID_JSON_NUMBER",
        "UNSUPPORTED_SHAPE",
        "INVALID_BEANS_STRUCTURE",
        "INVALID_CONDITIONS_SHAPE",
        "SANITIZATION_FAILED",
        "MALFORMED_EVIDENCE",
        "TARGET_CONNECTION_REFUSED",
        "TARGET_CONNECTION_FAILED",
        "REDIRECT_BLOCKED",
        "ENDPOINT_NOT_EXPOSED",
        "STARTUP_DATA_UNAVAILABLE",
        "UNEXPECTED_CONTENT_TYPE",
        "DUPLICATE_JSON_KEY",
        "UNSAFE_OUTPUT",
        "UNTRUSTED_OUTPUT",
        "INTERNAL_ERROR",
        "INVALID_HTTP_RESPONSE",
        "FIXTURE_PATH_DENIED",
        "REQUEST_CANCELLED",
    ]
)
_DIAGNOSTIC_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def safe_code(code: str) -> str:
    if isinstance(code, str) and (
        code in SAFE_CODES or _DIAGNOSTIC_CODE.fullmatch(code)
    ):
        return code
    return "MALFORMED_EVIDENCE"


def error(
    code: str, endpoint: str | None = None, status: int | None = None
) -> dict[str, Any]:
    code = safe_code(code)
    endpoint = (
        endpoint
        if isinstance(endpoint, str)
        and endpoint
        in {
            "beans",
            "conditions",
            "health",
            "mappings",
            "flyway",
            "liquibase",
            "startup",
            "openapi",
            "env",
            "configprops",
            "env-property",
            "health-liveness",
            "health-readiness",
            "info",
        }
        else None
    )
    status = status if type(status) is int and 100 <= status <= 599 else None
    action = (
        "reviewAuthorization"
        if code in ("ENDPOINT_UNAUTHORIZED", "REDIRECT_BLOCKED")
        else "reviewExposure"
        if code in ("ENDPOINT_UNAVAILABLE", "ENDPOINT_NOT_EXPOSED")
        else "configureStartup"
        if code == "STARTUP_DATA_UNAVAILABLE"
        else "configureCapability"
        if code == "CAPABILITY_DISABLED"
        else "reviewTarget"
    )
    return {
        "code": code,
        "endpointId": endpoint,
        "httpStatus": status,
        "retryable": False,
        "message": DIAGNOSTIC_MESSAGE,
        "remediation": {
            "recipeId": code,
            "action": action,
            "configKeys": ["management.endpoints.web.exposure.include"]
            if action == "reviewExposure"
            else [],
            "steps": [REVIEW_STARTUP]
            if code == "STARTUP_DATA_UNAVAILABLE"
            else [REVIEW_AUTHORIZATION]
            if action == "reviewAuthorization"
            else [REVIEW_TARGET],
            "requiresApproval": True,
            "retryAfterMs": None,
        },
    }


def envelope(target: str = "local") -> dict[str, Any]:
    return {
        "schemaVersion": "2",
        "status": "ok",
        "source": {
            "targetId": target,
            "snapshotId": None,
            "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "endpointIds": [],
            "bootVersion": None,
            "adapterVersion": "beans-1",
            "policyVersion": "beans-1",
        },
        "data": {
            "items": [],
            "missingBeanAnalysis": {
                "state": "notNeeded",
                "conditions": [],
                "blockingPaths": [],
            },
        },
        "totalMatched": 0,
        "truncated": False,
        "truncationReasons": [],
        "coverage": {
            "state": "complete",
            "scope": "registered target Bean contexts",
            "gaps": [],
        },
        "warnings": [],
        "redactions": [],
        "nextCursor": None,
        "nextAction": {
            "kind": "done",
            "reasonCode": "COMPLETE",
            "filterSuggestions": [],
        },
        "errors": [],
    }


def partial(result: dict[str, Any], gaps: list[str]) -> None:
    if gaps:
        result["status"] = "partial"
        result["coverage"]["state"] = "partial"
        result["coverage"]["gaps"] = sorted(set(result["coverage"]["gaps"] + gaps))[:10]
        result["nextAction"] = {
            "kind": "refine",
            "reasonCode": "EVIDENCE_INCOMPLETE",
            "filterSuggestions": [],
        }


def failed(
    code: str,
    target: str = "local",
    *,
    endpoint: str | None = None,
    http_status: int | None = None,
) -> dict[str, Any]:
    code = safe_code(code)
    target = (
        target
        if isinstance(target, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target)
        else "local"
    )
    result = envelope(target)
    result.update(
        status="invalid"
        if code == "INVALID_ARGUMENT"
        else "denied"
        if code in ("ENDPOINT_UNAUTHORIZED", "TARGET_NOT_REGISTERED")
        else "unavailable",
        data=None,
        totalMatched=None,
        errors=[error(code, endpoint, http_status)],
    )
    result["coverage"]["state"] = "unavailable"
    result["nextAction"] = {
        "kind": "remediate",
        "reasonCode": code,
        "filterSuggestions": [],
    }
    return result


def _check(
    observation: ParsedObservation, target: str, snapshot: str, endpoint: str
) -> None:
    if (observation.target_id, observation.snapshot_id, observation.endpoint_id) != (
        target,
        snapshot,
        endpoint,
    ):
        raise NormalizationError("OBSERVATION_IDENTITY_MISMATCH")
    if observation.error_code is not None:
        raise NormalizationError("ENDPOINT_UNAVAILABLE")
    if observation.body is None or not (
        observation.http_status == 200
        or (observation.http_status == 503 and endpoint in HEALTH_ENDPOINTS)
    ):
        raise NormalizationError(
            "ENDPOINT_UNAUTHORIZED"
            if observation.http_status in (401, 403)
            else "ENDPOINT_UNAVAILABLE"
        )
    if (
        not isinstance(observation.observation_id, str)
        or not observation.observation_id
        or len(observation.observation_id) > 4096
    ):
        raise NormalizationError("OBSERVATION_IDENTITY_MISMATCH")
    try:
        date = datetime.fromisoformat(observation.observed_at.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError, AttributeError):
        raise NormalizationError("INVALID_OBSERVATION_TIME") from None
    validate_tree(observation.body)
