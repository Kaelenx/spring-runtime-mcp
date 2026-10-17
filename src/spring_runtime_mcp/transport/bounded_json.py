"""Strict bounded parsing for offline observations and future safe readers."""

from __future__ import annotations

import json
import math
from typing import Any

from ..models.common import NormalizationError


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NormalizationError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise NormalizationError("INVALID_JSON_NUMBER")


def _integer(value: str) -> int:
    # No diagnostic identifier or checksum needs unbounded arbitrary precision.
    # Limit before int() allocates a huge integer or hits interpreter limits.
    if len(value.lstrip("-")) > 128:
        raise NormalizationError("INVALID_JSON_NUMBER")
    return int(value)


def loads_bounded(
    data: bytes,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    max_depth: int = 64,
    max_nodes: int = 100_000,
) -> dict[str, Any]:
    if len(data) > max_bytes:
        raise NormalizationError("RESPONSE_TOO_LARGE")
    try:
        text = data.decode("utf-8")
        depth = nodes = index = 0
        while index < len(text):
            char = text[index]
            if char == '"':
                nodes += 1
                index += 1
                while index < len(text):
                    if text[index] == "\\":
                        index += 2
                    elif text[index] == '"':
                        index += 1
                        break
                    else:
                        index += 1
            elif char in "{[":
                depth += 1
                nodes += 1
                index += 1
            elif char in "}]":
                depth -= 1
                index += 1
            elif char in " \t\n\r,:":
                index += 1
            else:
                nodes += 1
                while index < len(text) and text[index] not in " \t\r\n,]}:":
                    index += 1
            if depth > max_depth or nodes > max_nodes:
                raise NormalizationError("NORMALIZATION_LIMIT")
        value = json.loads(
            text, object_pairs_hook=_pairs, parse_constant=_constant, parse_int=_integer
        )
        if not isinstance(value, dict):
            raise NormalizationError("UNSUPPORTED_SHAPE")
        validate_tree(
            value, max_bytes=max_bytes, max_depth=max_depth, max_nodes=max_nodes
        )
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, NormalizationError):
            raise
        raise NormalizationError("INVALID_JSON") from None


def validate_tree(
    value: Any,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    max_depth: int = 64,
    max_nodes: int = 100_000,
) -> None:
    stack = [(value, 0)]
    seen: set[int] = set()
    nodes = size = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            raise NormalizationError("NORMALIZATION_LIMIT")
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                raise NormalizationError("NON_TREE_JSON")
            seen.add(id(item))
            if nodes + len(item) > max_nodes:
                raise NormalizationError("NORMALIZATION_LIMIT")
            if isinstance(item, dict):
                if any(not isinstance(k, str) for k in item):
                    raise NormalizationError("INVALID_JSON")
                stack.extend((k, depth + 1) for k in item)
                stack.extend((v, depth + 1) for v in item.values())
            else:
                stack.extend((v, depth + 1) for v in item)
            size += len(item) * 2 + 2
        elif isinstance(item, str):
            if len(item) > max_bytes:
                raise NormalizationError("RESPONSE_TOO_LARGE")
            try:
                size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            except UnicodeError:
                raise NormalizationError("INVALID_JSON") from None
        elif item is None:
            size += 4
        elif type(item) is bool:
            size += 4 if item else 5
        elif type(item) is int:
            if item.bit_length() > 426:
                raise NormalizationError("INVALID_JSON_NUMBER")
            integer_text = str(item)
            if len(integer_text.lstrip("-")) > 128:
                raise NormalizationError("INVALID_JSON_NUMBER")
            size += len(integer_text)
        elif isinstance(item, float) and math.isfinite(item):
            size += len(json.dumps(item, allow_nan=False))
        else:
            raise NormalizationError("INVALID_JSON")
        if size > max_bytes:
            raise NormalizationError("RESPONSE_TOO_LARGE")
