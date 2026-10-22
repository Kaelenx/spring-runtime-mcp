"""Shared envelope, observation check, and byte-budget paging for diagnostic tools."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from ..models.common import NormalizationError
from ..snapshots import SnapshotStore
from ..transport.responses import serialize_response
from .inspect_beans import Services, _check, failed, partial


def shell(target: str, scope: str, adapter: str) -> dict[str, Any]:
    return {
        "schemaVersion": "2",
        "status": "ok",
        "source": {
            "targetId": target,
            "snapshotId": None,
            "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "endpointIds": [],
            "bootVersion": None,
            "adapterVersion": adapter,
            "policyVersion": adapter,
        },
        "data": {"items": []},
        "totalMatched": 0,
        "truncated": False,
        "truncationReasons": [],
        "coverage": {"state": "complete", "scope": scope, "gaps": []},
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


def stamp(
    result: dict[str, Any], observation: Any, endpoint: str, snapshot: str
) -> None:
    result["source"].update(
        snapshotId=snapshot,
        endpointIds=sorted({*result["source"]["endpointIds"], endpoint}),
        observedAt=datetime.fromisoformat(
            observation.observed_at.replace("Z", "+00:00")
        )
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    )


async def read_checked(
    services: Services, target: str, endpoint: str, snapshot: str
) -> Any:
    if target not in services.targets or services.catalog.target_id != target:
        raise NormalizationError("TARGET_NOT_REGISTERED")
    if endpoint in {"env", "configprops"} and services.config_policy is None:
        from ..transport.response_gate import EndpointReadError

        raise EndpointReadError("CAPABILITY_DISABLED", endpoint)
    observation = await asyncio.wait_for(
        services.reader.read(target, endpoint, snapshot_id=snapshot),
        timeout=15,
    )
    _check(observation, target, snapshot, endpoint)
    return observation


def finalize(result: dict[str, Any], services: Services, query: Any) -> dict[str, Any]:
    """Slice items to the query limit and the serialized response budget."""
    result = services.sanitizer.sanitize(result)
    if not isinstance(result.get("data"), dict):
        return result
    items = list(result["data"].get("items") or [])
    count = min(len(items), query.max_results)
    while True:
        page = deepcopy(result)
        page["data"]["items"] = items[:count]
        remaining = items[count:]
        if remaining:
            page["truncated"] = True
            page["truncationReasons"] = sorted(
                set(page["truncationReasons"] + ["MAX_RESULTS"])
            )[:8]
            partial(page, ["MAX_RESULTS"])
            page["nextAction"] = {
                "kind": "page",
                "reasonCode": "MORE_RESULTS",
                "filterSuggestions": [],
            }
            page["nextCursor"] = "x" * 64
        try:
            serialize_response(page, "\0" * 128, sanitizer=services.sanitizer)
            break
        except NormalizationError as exc:
            if exc.code != "RESPONSE_TOO_LARGE":
                raise
            if count <= 1:
                return failed("RESPONSE_TOO_LARGE", query.target_id)
            count //= 2
    if items[count:]:
        deadline = (
            services.snapshots.deadline(query.cursor, query) if query.cursor else None
        )
        continuation = deepcopy(result)
        continuation["data"]["items"] = items[count:]
        page["nextCursor"] = services.snapshots.put(
            services.sanitizer.sanitize(continuation),
            query,
            sanitizer=services.sanitizer,
            expires_at=deadline,
        )
        serialize_response(page, "\0" * 128, sanitizer=services.sanitizer)
    return page


def resume(services: Services, query: Any) -> dict[str, Any]:
    stored = services.snapshots.get(query.cursor, query, sanitizer=services.sanitizer)
    return finalize(stored, services, query)


# Re-export for handlers that only need the store type in annotations.
__all__ = ["SnapshotStore", "finalize", "read_checked", "resume", "shell", "stamp"]
