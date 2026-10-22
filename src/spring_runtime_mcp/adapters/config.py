"""Normalize env precedence and configuration property bindings."""

from __future__ import annotations

import re
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from ..models.common import AnalysisBudget

from ..contracts.definitions import glob_matches
from ..models.queries import ConfigQuery
from ..policy.sanitization import MARKER, SanitizerPipeline, sensitive_key
from ..policy.configuration import ConfigReadPolicy

_FILE = re.compile(r"class path resource \[([A-Za-z0-9_$./-]+)\](?: - (\d+):(\d+))?")
_MASK = re.compile(r"^\*+$")


def _kind(source_name: str) -> str:
    lowered = source_name.casefold().replace("_", "")
    if "applicationconfig" in lowered or "classpath" in lowered:
        return "configurationFile"
    if "systemenvironment" in lowered:
        return "environmentVariable"
    if "systemproperties" in lowered:
        return "systemProperty"
    if "commandline" in lowered:
        return "commandLine"
    return "other" if source_name else "unknown"


def _origin(raw: Any, source_name: str) -> dict[str, Any]:
    kind = _kind(source_name)
    file = line = column = None
    if isinstance(raw, str):
        matched = _FILE.search(raw)
        if matched:
            file = matched.group(1).rsplit("/", 1)[-1][:256]
            line = int(matched.group(2)) if matched.group(2) else None
            column = int(matched.group(3)) if matched.group(3) else None
            kind = "configurationFile"
    return {
        "kind": kind,
        "file": file,
        "line": line,
        "column": column,
        "environmentVariable": None,
    }


_NESTED_DEPTH = 4
_NESTED_LEAVES = 500


def _nested_leaves(
    prefix: str,
    node: dict[str, Any],
    *,
    gaps: set[str],
    budget: list[int],
    depth: int = 0,
) -> Iterator[tuple[str, Any]]:
    """Expand nested config objects to dotted leaves under fixed budgets."""
    for leaf in sorted(node):
        if not isinstance(leaf, str):
            gaps.add("CONFIGPROPS_NESTED_UNSUPPORTED")
            continue
        key = f"{prefix}.{leaf}" if prefix else leaf
        value = node[leaf]
        if isinstance(value, dict):
            if depth >= _NESTED_DEPTH:
                gaps.add("CONFIGPROPS_NESTED_LIMIT")
                continue
            yield from _nested_leaves(
                key, value, gaps=gaps, budget=budget, depth=depth + 1
            )
            continue
        if isinstance(value, list):
            gaps.add("CONFIGPROPS_NESTED_UNSUPPORTED")
            continue
        if budget[0] >= _NESTED_LEAVES:
            gaps.add("CONFIGPROPS_NESTED_LIMIT")
            continue
        budget[0] += 1
        yield key, value


def _selected(query: ConfigQuery, key: str) -> bool:
    if query.prefix and key != query.prefix and not key.startswith(query.prefix + "."):
        return False
    return not (
        query.property_key_pattern and not glob_matches(query.property_key_pattern, key)
    )


def _project(
    raw: Any, include: bool, key: str, pipeline: SanitizerPipeline
) -> tuple[Any, str]:
    if not include:
        return None, "notReported"
    if sensitive_key(key) or (
        isinstance(raw, str) and (_MASK.fullmatch(raw) or pipeline.high_risk(raw))
    ):
        return MARKER, "redacted"
    if isinstance(raw, str):
        if any(char.isspace() for char in raw) or any(ord(char) < 32 for char in raw):
            return MARKER, "redacted"
        return raw[:512], "visible"
    if isinstance(raw, bool) or type(raw) is int:
        return raw, "visible"
    if isinstance(raw, float) and raw == raw:
        return raw, "visible"
    return None, "notReported"


def normalize_config(
    env: Any | None,
    configprops: Any | None,
    query: ConfigQuery,
    pipeline: SanitizerPipeline,
    *,
    policy: ConfigReadPolicy | None = None,
) -> tuple[list[str] | None, list[dict[str, Any]], list[str]]:
    gaps: set[str] = set()
    analysis_budget = AnalysisBudget()
    profiles: list[str] | None = None
    rows: list[dict[str, Any]] = []
    if isinstance(env, dict):
        raw_profiles = env.get("activeProfiles")
        if isinstance(raw_profiles, list):
            profiles = [
                item
                for item in raw_profiles
                if isinstance(item, str)
                and item
                and not any(char.isspace() for char in item)
            ][:20]
        sources = env.get("propertySources")
        if not isinstance(sources, list):
            gaps.add("ENV_SOURCES_UNOBSERVED")
        else:
            seen: set[str] = set()
            for source in sources:
                if not isinstance(source, dict):
                    gaps.add("ENV_SOURCE_UNREADABLE")
                    continue
                raw_name = source.get("name")
                name = raw_name if isinstance(raw_name, str) else ""
                properties = source.get("properties")
                if not isinstance(properties, dict):
                    gaps.add("ENV_PROPERTIES_UNOBSERVED")
                    continue
                for key in sorted(properties):
                    analysis_budget.consume()
                    if not isinstance(key, str) or not _selected(query, key):
                        continue
                    if policy is not None and not policy.allows(key):
                        gaps.add("CONFIG_SCOPE_LIMITED")
                        continue
                    if query.context_id is not None:
                        continue
                    spec = properties[key]
                    raw_value = spec.get("value") if isinstance(spec, dict) else spec
                    origin = spec.get("origin") if isinstance(spec, dict) else None
                    value, state = _project(
                        raw_value,
                        query.include_values
                        and (policy is None or policy.allow_values),
                        key,
                        pipeline,
                    )
                    rows.append(
                        {
                            "endpointId": "env",
                            "contextId": None,
                            "parentContextId": None,
                            "propertyKey": key[:256],
                            "prefix": key.rsplit(".", 1)[0] if "." in key else None,
                            "configurationBean": None,
                            "effectiveValue": value,
                            "valueState": state,
                            "bindingValue": None,
                            "bindingState": "notReported",
                            "origin": _origin(origin, name),
                            "sourceKind": _origin(origin, name)["kind"],
                            "effective": "no" if key in seen else "yes",
                        }
                    )
                    seen.add(key)
    elif env is not None:
        gaps.add("ENV_SOURCES_UNOBSERVED")
    if isinstance(configprops, dict):
        contexts = configprops.get("contexts")
        if not isinstance(contexts, dict):
            gaps.add("CONFIGPROPS_UNOBSERVED")
        else:
            for context_name in sorted(contexts):
                if query.context_id and context_name != query.context_id:
                    continue
                context = contexts[context_name]
                beans = context.get("beans") if isinstance(context, dict) else None
                if not isinstance(beans, dict):
                    gaps.add("CONFIGPROPS_UNOBSERVED")
                    continue
                parent = (
                    context.get("parentId")
                    if isinstance(context.get("parentId"), str)
                    else None
                )
                for bean_name in sorted(beans):
                    bean = beans[bean_name]
                    if not isinstance(bean, dict):
                        continue
                    prefix = (
                        bean.get("prefix")
                        if isinstance(bean.get("prefix"), str)
                        else None
                    )
                    properties = bean.get("properties")
                    if not isinstance(properties, dict):
                        gaps.add("CONFIGPROPS_PROPERTIES_UNOBSERVED")
                        continue
                    budget = [0]
                    for key, raw_value in _nested_leaves(
                        prefix or "", properties, gaps=gaps, budget=budget
                    ):
                        analysis_budget.consume()
                        if not _selected(query, key):
                            continue
                        if policy is not None and not policy.allows(key):
                            gaps.add("CONFIG_SCOPE_LIMITED")
                            continue
                        value, state = _project(
                            raw_value,
                            query.include_values
                            and (policy is None or policy.allow_values),
                            key,
                            pipeline,
                        )
                        target = next(
                            (
                                row
                                for row in rows
                                if row["propertyKey"] == key
                                and row["effective"] == "yes"
                            ),
                            None,
                        )
                        if target is None:
                            rows.append(
                                {
                                    "endpointId": "configprops",
                                    "contextId": context_name
                                    if isinstance(context_name, str)
                                    else None,
                                    "parentContextId": parent,
                                    "propertyKey": key[:256],
                                    "prefix": prefix,
                                    "configurationBean": str(bean_name)[:256],
                                    "effectiveValue": None,
                                    "valueState": "notReported",
                                    "bindingValue": value,
                                    "bindingState": state,
                                    "origin": {
                                        "kind": "unknown",
                                        "file": None,
                                        "line": None,
                                        "column": None,
                                        "environmentVariable": None,
                                    },
                                    "sourceKind": "unknown",
                                    "effective": "unknown",
                                }
                            )
                        else:
                            if target["configurationBean"] is not None:
                                target = deepcopy(target)
                                rows.append(target)
                            target["configurationBean"] = str(bean_name)[:256]
                            target["prefix"] = prefix or target["prefix"]
                            target["bindingValue"] = value
                            target["bindingState"] = state
                            target["contextId"] = (
                                context_name if isinstance(context_name, str) else None
                            )
                            target["parentContextId"] = parent
    elif configprops is not None:
        gaps.add("CONFIGPROPS_UNOBSERVED")
    items = []
    for index, row in enumerate(rows, start=1):
        row["id"] = f"config_{index:06d}"
        items.append(row)
    return profiles if policy is None or policy.profiles else None, items, sorted(gaps)
