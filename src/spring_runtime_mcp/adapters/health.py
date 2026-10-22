"""Normalize Boot 2.x details trees and 3.x/4.x component trees."""

from __future__ import annotations

import re
from typing import Any

from ..models.common import AnalysisBudget

_ABNORMAL = {"DOWN", "OUT_OF_SERVICE", "UNKNOWN"}
_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]{0,255}\Z")


def _children(node: dict[str, Any]) -> dict[str, Any]:
    components = node.get("components")
    if isinstance(components, dict):
        return components
    details = node.get("details")
    if isinstance(details, dict):
        return {
            str(key): value
            for key, value in details.items()
            if isinstance(value, dict) and "status" in value
        }
    return {}


def _scalars(
    node: dict[str, Any], gaps: set[str], *, report_limit: bool
) -> list[dict[str, Any]]:
    details = node.get("details")
    if not isinstance(details, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, value in sorted(details.items(), key=lambda item: str(item[0])):
        if isinstance(value, dict) and "status" in value:
            continue
        finite = (
            isinstance(value, bool) or not isinstance(value, float) or value == value
        )
        if (isinstance(value, (str, int, float, bool)) or value is None) and finite:
            rows.append({"name": str(key)[:128], "value": value})
    if len(rows) > 10:
        if report_limit:
            gaps.add("HEALTH_DETAILS_LIMIT")
        return rows[:10]
    return rows


def _error_type(node: dict[str, Any]) -> str | None:
    details = node.get("details")
    raw = details.get("error") if isinstance(details, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    tokens = raw.split(":", 1)[0].strip().split()
    if not tokens:
        return None
    token = tokens[-1]
    if _CLASS.fullmatch(token):
        return token
    return None


def _codes(path: tuple[str, ...], hidden: bool) -> list[str]:
    if hidden:
        return ["REVIEW_HEALTH_DETAILS_VISIBILITY"]
    names = " ".join(path).casefold()
    if any(token in names for token in ("db", "database", "datasource", "sql")):
        return ["REVIEW_DATABASE_CONNECTIVITY"]
    if any(token in names for token in ("disk", "diskspace")):
        return ["REVIEW_DISK_SPACE"]
    if any(token in names for token in ("redis", "kafka", "rabbit", "mongo")):
        return ["REVIEW_MESSAGE_DEPENDENCY"]
    return ["REVIEW_HEALTH_COMPONENT"]


def normalize_health(
    body: Any,
    *,
    component_path: tuple[str, ...],
    include_details: bool,
    include_healthy: bool,
    max_depth: int,
) -> tuple[str | None, list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict):
        raise ValueError("INVALID_HEALTH_SHAPE")
    overall = body.get("status") if isinstance(body.get("status"), str) else None
    found: list[dict[str, Any]] = []
    gaps: set[str] = set()
    budget = AnalysisBudget()

    def walk(node: Any, path: tuple[str, ...], depth: int) -> None:
        budget.consume()
        if not isinstance(node, dict):
            gaps.add("HEALTH_COMPONENT_UNREADABLE")
            return
        children = _children(node)
        if "components" in node and not isinstance(node["components"], dict):
            gaps.add("HEALTH_COMPONENT_UNREADABLE")
        raw_status = node.get("status")
        status = raw_status if isinstance(raw_status, str) and raw_status else "UNKNOWN"
        if not isinstance(raw_status, str) or not raw_status:
            gaps.add("HEALTH_STATUS_UNOBSERVED")
        scalars = _scalars(node, gaps, report_limit=include_details)
        # Root UP is an observed aggregate status, not evidence that components
        # are absent. Explicit components={} is a reported empty inventory.
        hidden_root = (
            depth == 0 and "components" not in node and not children and not scalars
        )
        hidden = hidden_root or (status != "UP" and not children and not scalars)
        if hidden:
            gaps.add("HEALTH_DETAILS_NOT_VISIBLE")
        if depth > max_depth:
            gaps.add("HEALTH_DEPTH_LIMIT")
            return
        selected = (
            not component_path or path[: len(component_path)] == component_path
        ) and (depth > 0 or not children)
        visible = not component_path or path[: len(component_path)] == component_path
        if selected and visible and (include_healthy or status != "UP" or hidden):
            found.append(
                {
                    "path": path,
                    "status": status[:64],
                    "details": scalars if include_details else [],
                    "errorType": _error_type(node),
                    "remediationCodes": _codes(path, hidden),
                    "childrenComplete": not hidden and depth < max_depth,
                }
            )
        if depth >= max_depth and children:
            gaps.add("HEALTH_DEPTH_LIMIT")
            return
        for name in sorted(children):
            if (
                len(path) + 1 > 16
                or len(name) > 128
                or any(ord(char) < 32 for char in name)
            ):
                gaps.add("HEALTH_COMPONENT_UNREADABLE")
                continue
            walk(children[name], path + (name,), depth + 1)

    walk(body, (), 0)
    items = []
    previous: dict[tuple[str, ...], str] = {}
    for index, row in enumerate(found, start=1):
        identifier = f"health_{index:06d}"
        path = row["path"]
        previous[path] = identifier
        parent = previous.get(path[:-1]) if path else None
        items.append(
            {
                "id": identifier,
                "componentPath": list(path),
                "parentId": parent,
                "status": row["status"],
                "details": row["details"],
                "errorType": row["errorType"],
                "remediationCodes": row["remediationCodes"],
                "childrenComplete": row["childrenComplete"],
            }
        )
    return overall[:64] if isinstance(overall, str) else None, items, sorted(gaps)
