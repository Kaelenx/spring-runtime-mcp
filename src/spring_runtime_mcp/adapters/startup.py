"""Normalize ApplicationStartup timelines without posting to the startup endpoint."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ..contracts.definitions import glob_matches

_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?\Z")


def _nanos(text: str) -> int | None:
    matched = _DURATION.fullmatch(text)
    if matched is None or not any(matched.groups()):
        return None
    hours = Decimal(matched.group(1) or 0)
    minutes = Decimal(matched.group(2) or 0)
    seconds = Decimal(matched.group(3) or 0)
    return int((hours * 3600 + minutes * 60 + seconds) * Decimal(1_000_000_000))


def _bottleneck(name: str) -> str:
    if "beans.instantiate" in name:
        return "BEAN_INSTANTIATION"
    if "base-packages.scan" in name or "component-scan" in name:
        return "PACKAGE_SCAN"
    if "auto-config" in name or "autoconfigure" in name:
        return "AUTO_CONFIGURATION"
    return "OTHER" if name else "UNKNOWN"


def normalize_startup(
    body: Any,
    *,
    step_name_pattern: str | None,
    bean_name_pattern: str | None,
    min_duration_ms: float,
    max_depth: int,
) -> tuple[str | None, int | float | None, str, bool, list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("timeline"), dict):
        raise ValueError("INVALID_STARTUP_SHAPE")
    events = body["timeline"].get("events")
    if not isinstance(events, list):
        raise ValueError("INVALID_STARTUP_SHAPE")
    version = body.get("springBootVersion")
    version = version if isinstance(version, str) and len(version) <= 64 else None
    gaps: set[str] = set()
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        if len(order) >= 500:
            gaps.add("STARTUP_NODE_LIMIT")
            break
        if not isinstance(event, dict) or not isinstance(
            event.get("startupStep"), dict
        ):
            gaps.add("STARTUP_EVENT_UNREADABLE")
            continue
        step = event["startupStep"]
        raw_id = step.get("id")
        if type(raw_id) is not int or raw_id < 0:
            gaps.add("STARTUP_EVENT_UNREADABLE")
            continue
        step_id = str(raw_id)
        if step_id in records:
            gaps.add("STARTUP_DUPLICATE_STEP")
            records[step_id]["relationState"] = "duplicate"
            continue
        nanos = (
            _nanos(event["duration"])
            if isinstance(event.get("duration"), str)
            else None
        )
        if nanos is None:
            gaps.add("STARTUP_DURATION_UNPARSED")
        parent = step.get("parentId")
        parent_id = str(parent) if type(parent) is int and parent >= 0 else None
        tags = []
        raw_tags = step.get("tags", [])
        if isinstance(raw_tags, list):
            for tag in raw_tags[:10]:
                if not isinstance(tag, dict) or not isinstance(tag.get("key"), str):
                    continue
                value = tag.get("value")
                tags.append(
                    {
                        "key": tag["key"][:128],
                        "value": value[:256] if isinstance(value, str) else None,
                    }
                )
        name = step.get("name") if isinstance(step.get("name"), str) else ""
        records[step_id] = {
            "stepId": step_id,
            "parentStepId": parent_id,
            "name": name[:256],
            "startedAt": event.get("startTime")
            if isinstance(event.get("startTime"), str)
            else None,
            "durationNanos": None if nanos is None else str(nanos),
            "durationMs": None if nanos is None else nanos / 1_000_000,
            "tags": tags,
            "bottleneckCode": _bottleneck(name),
            "relationState": "resolved",
        }
        order.append(step_id)
    for step_id, row in records.items():
        parent = row["parentStepId"]
        if parent is None:
            continue
        if parent not in records:
            row["relationState"] = "orphan"
            gaps.add("STARTUP_ORPHAN_STEP")
            continue
        seen = {step_id}
        cursor = parent
        while cursor is not None:
            if cursor in seen:
                row["relationState"] = "cycle"
                gaps.add("STARTUP_CYCLE")
                break
            seen.add(cursor)
            cursor = records.get(cursor, {}).get("parentStepId")
    items: list[dict[str, Any]] = []
    for step_id in order:
        row = records[step_id]
        ancestors: list[str] = []
        cursor = row["parentStepId"]
        visited: set[str] = set()
        while cursor and cursor not in visited and len(ancestors) < 16:
            visited.add(cursor)
            ancestors.append(cursor)
            cursor = records.get(cursor, {}).get("parentStepId")
        if len(ancestors) > max_depth:
            gaps.add("STARTUP_DEPTH_LIMIT")
            continue
        if step_name_pattern and not glob_matches(step_name_pattern, row["name"]):
            continue
        if bean_name_pattern and not any(
            tag["key"] == "beanName"
            and isinstance(tag["value"], str)
            and glob_matches(bean_name_pattern, tag["value"])
            for tag in row["tags"]
        ):
            continue
        duration = row["durationMs"]
        if duration is not None and duration < min_duration_ms:
            continue
        row["ancestorIds"] = ancestors
        items.append(row)
    items.sort(
        key=lambda row: (
            -(row["durationMs"] if isinstance(row["durationMs"], (int, float)) else -1),
            row["stepId"],
        )
    )
    durations = [
        row["durationMs"]
        for row in records.values()
        if isinstance(row["durationMs"], (int, float))
    ]
    total = max(durations) if durations else None
    complete = not gaps
    basis = "timelineSpan" if total is not None else "unknown"
    return version, total, basis, complete, items, sorted(gaps)
