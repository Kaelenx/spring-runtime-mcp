"""Validate bounded source evidence without reading or executing source files."""

import json
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from ..models.common import AnalysisBudget, NormalizationError
from ..models.source_catalog import SourceCatalog, SourceProducer, TypeFact
from ..transport.bounded_json import validate_tree

_ID = re.compile(r"[A-Za-z_$][A-Za-z0-9_.$]*(?:#[A-Za-z_$][A-Za-z0-9_$]*)?\Z")


def _text(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 256 or not _ID.fullmatch(value):
        raise NormalizationError("INVALID_SOURCE_CATALOG")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 1000:
        raise NormalizationError("INVALID_SOURCE_CATALOG")
    return tuple(_text(v) for v in value)


def load_source_catalog(
    payload: Mapping[str, Any], *, target_id: str, budget: AnalysisBudget
) -> SourceCatalog:
    validate_tree(dict(payload), max_bytes=262144)
    if (
        set(payload) - {"targetId", "sourceRevision", "types", "producers"}
        or payload.get("targetId") != target_id
    ):
        raise NormalizationError("SOURCE_TARGET_MISMATCH")
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 262144:
        raise NormalizationError("SOURCE_CATALOG_TOO_LARGE")
    rows, producers = payload.get("types", []), payload.get("producers", [])
    if (
        not isinstance(rows, list)
        or not isinstance(producers, list)
        or len(rows) + len(producers) > 1000
    ):
        raise NormalizationError("INVALID_SOURCE_CATALOG")
    types: dict[str, TypeFact] = {}
    for row in rows:
        budget.consume()
        if not isinstance(row, dict) or set(row) != {"name", "directSupertypes"}:
            raise NormalizationError("INVALID_SOURCE_CATALOG")
        name = _text(row["name"])
        if name in types:
            raise NormalizationError("DUPLICATE_SOURCE_TYPE")
        types[name] = TypeFact(name, _strings(row["directSupertypes"]))
    parsed = []
    keys = {
        "contextId",
        "beanName",
        "aliases",
        "declaredType",
        "implementationType",
        "configurationClass",
        "factoryMethod",
        "conditionSources",
        "sourceFile",
        "sourceLine",
    }
    for i, row in enumerate(producers):
        budget.consume()
        if not isinstance(row, dict) or set(row) != keys:
            raise NormalizationError("INVALID_SOURCE_CATALOG")
        file = row["sourceFile"]
        if (
            not isinstance(file, str)
            or file in ("", ".")
            or len(file) > 256
            or "\\" in file
            or ":" in file
            or any(ord(c) < 32 for c in file)
            or PurePosixPath(file).is_absolute()
            or ".." in PurePosixPath(file).parts
        ):
            raise NormalizationError("INVALID_SOURCE_PATH")
        line = row["sourceLine"]
        if type(line) is not int or line < 1:
            raise NormalizationError("INVALID_SOURCE_LINE")

        current = row

        def optional(key: str, record: dict[str, Any] = current) -> str | None:
            return None if record[key] is None else _text(record[key])

        parsed.append(
            SourceProducer(
                f"source_producer_{i}",
                _text(row["contextId"]),
                _text(row["beanName"]),
                _strings(row["aliases"]),
                _text(row["declaredType"]),
                optional("implementationType"),
                _text(row["configurationClass"]),
                optional("factoryMethod"),
                _strings(row["conditionSources"]),
                file,
                line,
                (f"source_evidence_{i}",),
            )
        )
    revision = payload.get("sourceRevision")
    if revision is not None and (
        not isinstance(revision, str)
        or len(revision) > 128
        or any(ord(c) < 32 for c in revision)
    ):
        raise NormalizationError("INVALID_SOURCE_REVISION")
    return SourceCatalog(
        target_id, revision, MappingProxyType(types), tuple(parsed), ()
    )
