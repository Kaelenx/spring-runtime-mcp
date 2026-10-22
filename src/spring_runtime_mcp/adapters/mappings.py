"""Normalize MVC dispatcherServlets and WebFlux dispatcherHandlers."""

from __future__ import annotations

import re
from typing import Any

from ..models.common import AnalysisBudget

from ..contracts.definitions import glob_matches

_PREDICATE_CONDITION = re.compile(
    r"(?P<kind>consumes|produces|headers|params)\s*\[(?P<value>.*)\]\Z"
)
_PREDICATE_KIND = {
    "consumes": "consumes",
    "produces": "produces",
    "headers": "header",
    "params": "param",
}
_MAX_PREDICATE_LENGTH = 4096
_MAX_PREDICATE_PIECES = 32
_MAX_PREDICATE_PATHS = 16
_MAX_PREDICATE_CONDITIONS = 16


def _parse_predicate(
    raw: Any,
) -> tuple[list[str], list[str], list[dict[str, Any]], bool]:
    """Parse one bounded WebFlux predicate string without executing it."""
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_PREDICATE_LENGTH:
        return [], [], [], False
    text = raw.strip()
    if len(text) >= 2 and text[0] in "{(" and text[-1] in ")}":
        text = text[1:-1]
    pieces = [piece.strip() for piece in re.split(r",|&&", text)]
    pieces = [piece for piece in pieces if piece]
    if not pieces or len(pieces) > _MAX_PREDICATE_PIECES:
        return [], [], [], False
    paths: list[str] = []
    methods: list[str] = []
    conditions: list[dict[str, Any]] = []
    for piece in pieces:
        token, separator, rest = piece.partition(" ")
        if token.upper() in _METHODS:
            if methods:
                return [], [], [], False
            methods.append(token.upper())
            if not separator:
                continue
            rest = rest.strip()
            if rest.startswith("[") and rest.endswith("]"):
                rest = rest[1:-1].strip()
            if not (rest.startswith("/") and len(rest) <= 1024):
                return [], [], [], False
            if len(paths) >= _MAX_PREDICATE_PATHS:
                return [], [], [], False
            paths.append(rest)
            continue
        if piece.startswith("/") and 1 < len(piece) <= 1024:
            if len(paths) >= _MAX_PREDICATE_PATHS:
                return [], [], [], False
            paths.append(piece)
            continue
        matched = _PREDICATE_CONDITION.fullmatch(piece)
        if matched is None or len(conditions) >= _MAX_PREDICATE_CONDITIONS:
            return [], [], [], False
        value = matched.group("value").strip()
        if len(value) > 256:
            return [], [], [], False
        conditions.append(
            {
                "kind": _PREDICATE_KIND[matched.group("kind")],
                "name": None,
                "value": value or None,
                "negated": False,
            }
        )
    if not (paths or methods or conditions):
        return [], [], [], False
    return paths, methods, conditions, True


_METHODS = {
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
}


def _conditions(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    complete = True
    for kind, key in (
        ("consumes", "consumes"),
        ("produces", "produces"),
        ("header", "headers"),
        ("param", "params"),
    ):
        values = raw.get(key, [])
        if not isinstance(values, list):
            complete = False
            continue
        for value in values:
            if len(rows) >= 16:
                complete = False
                break
            if isinstance(value, dict):
                media = value.get("mediaType", value.get("value"))
                rows.append(
                    {
                        "kind": kind,
                        "name": value.get("name")
                        if isinstance(value.get("name"), str)
                        else None,
                        "value": media
                        if isinstance(media, str) and len(media) <= 256
                        else None,
                        "negated": bool(value.get("negated", False)),
                    }
                )
            elif isinstance(value, str) and len(value) <= 256:
                rows.append(
                    {"kind": kind, "name": None, "value": value, "negated": False}
                )
    return rows, complete


def normalize_mappings(
    body: Any,
    *,
    path_pattern: str | None,
    http_method: str | None,
    controller_pattern: str | None,
    context_id: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(body, dict) or not isinstance(body.get("contexts"), dict):
        raise ValueError("INVALID_MAPPINGS_SHAPE")
    gaps: set[str] = set()
    budget = AnalysisBudget()
    found: list[dict[str, Any]] = []
    for context_name in sorted(body["contexts"]):
        if not isinstance(context_name, str):
            gaps.add("INVALID_CONTEXT_NAME")
            continue
        if context_id and context_name != context_id:
            continue
        context = body["contexts"][context_name]
        if not isinstance(context, dict):
            gaps.add("INVALID_CONTEXT")
            continue
        parent = context.get("parentId")
        mappings = context.get("mappings")
        if not isinstance(mappings, dict):
            gaps.add("MAPPINGS_UNOBSERVED")
            continue
        trees = (
            ("mvc", "dispatcherServlets"),
            ("webflux", "dispatcherHandlers"),
        )
        for stack, tree_name in trees:
            tree = mappings.get(tree_name)
            if tree is None:
                continue
            if not isinstance(tree, dict):
                gaps.add("MAPPINGS_UNOBSERVED")
                continue
            for dispatcher in sorted(tree):
                records = tree[dispatcher]
                if not isinstance(records, list):
                    gaps.add("MAPPINGS_UNOBSERVED")
                    continue
                for record in records:
                    budget.consume()
                    if not isinstance(record, dict):
                        gaps.add("MAPPING_RECORD_UNREADABLE")
                        continue
                    details = record.get("details")
                    method = (
                        details.get("handlerMethod")
                        if isinstance(details, dict)
                        else None
                    )
                    conditions_raw = (
                        details.get("requestMappingConditions")
                        if isinstance(details, dict)
                        else None
                    )
                    handler_function = (
                        details.get("handlerFunction")
                        if isinstance(details, dict)
                        else None
                    )
                    if not isinstance(handler_function, str):
                        nested = record.get("handlerFunction")
                        handler_function = nested if isinstance(nested, str) else None
                    class_name = (
                        method.get("className") if isinstance(method, dict) else None
                    )
                    method_name = (
                        method.get("name") if isinstance(method, dict) else None
                    )
                    descriptor = (
                        method.get("descriptor") if isinstance(method, dict) else None
                    )
                    handler = record.get("handler")
                    if (
                        not isinstance(class_name, str)
                        and isinstance(handler, str)
                        and "#" in handler
                    ):
                        class_name, _, method_name = handler.partition("#")
                    patterns = []
                    methods: list[str] = []
                    condition_rows: list[dict[str, Any]] = []
                    complete = isinstance(conditions_raw, dict)
                    methods_complete = isinstance(conditions_raw, dict)
                    predicate_parsed = False
                    if isinstance(conditions_raw, dict):
                        raw_patterns = conditions_raw.get("patterns")
                        raw_methods = conditions_raw.get("methods")
                        if isinstance(raw_patterns, list):
                            for item in raw_patterns:
                                if isinstance(item, str) and 0 < len(item) <= 1024:
                                    patterns.append(item)
                                else:
                                    complete = False
                                    gaps.add("MAPPING_PATTERN_UNREADABLE")
                            if len(patterns) > 16:
                                patterns = patterns[:16]
                                complete = False
                                gaps.add("MAPPING_PATH_LIMIT")
                        else:
                            complete = False
                            gaps.add("MAPPING_PATTERN_UNREADABLE")
                        if isinstance(raw_methods, list):
                            for item in raw_methods:
                                if isinstance(item, str) and item in _METHODS:
                                    methods.append(item)
                                else:
                                    complete = False
                                    methods_complete = False
                                    gaps.add("MAPPING_METHOD_UNREADABLE")
                            if len(methods) > 9:
                                methods = methods[:9]
                                complete = False
                                gaps.add("MAPPING_METHOD_LIMIT")
                        else:
                            complete = False
                            methods_complete = False
                            gaps.add("MAPPING_METHOD_UNREADABLE")
                        condition_rows, condition_complete = _conditions(conditions_raw)
                        complete = complete and condition_complete
                    elif isinstance(record.get("predicate"), str):
                        (
                            patterns,
                            methods,
                            condition_rows,
                            predicate_parsed,
                        ) = _parse_predicate(record["predicate"])
                        if predicate_parsed:
                            complete = True
                            methods_complete = True
                        else:
                            patterns = []
                            methods = []
                            condition_rows = []
                            complete = False
                            methods_complete = False
                            gaps.add("MAPPING_PREDICATE_UNPARSED")
                    else:
                        complete = False
                        methods_complete = False
                        gaps.add("MAPPING_CONDITIONS_UNOBSERVED")
                    if path_pattern and not any(
                        glob_matches(path_pattern, pattern) for pattern in patterns
                    ):
                        continue
                    unrestricted = not methods and methods_complete
                    if http_method and not unrestricted and http_method not in methods:
                        continue
                    if controller_pattern and not (
                        isinstance(class_name, str)
                        and glob_matches(controller_pattern, class_name)
                    ):
                        continue
                    found.append(
                        {
                            "contextId": context_name,
                            "parentContextId": parent
                            if isinstance(parent, str)
                            else None,
                            "stack": stack,
                            "dispatcherName": dispatcher
                            if isinstance(dispatcher, str)
                            else None,
                            "paths": patterns,
                            "httpMethods": methods,
                            "methodsUnrestricted": unrestricted,
                            "controllerClass": class_name
                            if isinstance(class_name, str)
                            else None,
                            "handlerMethod": method_name
                            if isinstance(method_name, str)
                            else None,
                            "descriptor": descriptor
                            if isinstance(descriptor, str)
                            else None,
                            "handlerKind": "method"
                            if isinstance(method, dict)
                            else "function"
                            if predicate_parsed or handler_function
                            else "unknown",
                            "conditions": condition_rows,
                            "matchingComplete": complete,
                        }
                    )
    items = []
    for index, row in enumerate(found, start=1):
        row["id"] = f"mapping_{index:06d}"
        items.append(row)
    return items, sorted(gaps)
