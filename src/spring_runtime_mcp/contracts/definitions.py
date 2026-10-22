"""Load the committed contract without introducing a runtime validator dependency."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal, cast, overload

from ..models.common import NormalizationError
from ..models.queries import (
    ConfigQuery,
    DriftQuery,
    ExpectedBean,
    ExpectedMigration,
    ExpectedMigrationVersion,
    ExpectedRoute,
    HealthQuery,
    InspectBeansQuery,
    MappingQuery,
    MigrationQuery,
    OpenApiQuery,
    StartupQuery,
)

TOOL = "springboot_inspect_beans"


@lru_cache(maxsize=1)
def _definitions() -> tuple[dict[str, Any], ...]:
    resource = files("spring_runtime_mcp").joinpath("schemas/tools-contract.json")
    bundle = json.loads(resource.read_text(encoding="utf-8"))
    definitions = tuple(bundle["tools"])
    if len(definitions) != 8 or [d["name"] for d in definitions] != sorted(
        d["name"] for d in definitions
    ):
        raise RuntimeError("INVALID_TOOL_REGISTRATION_ORDER")
    return definitions


def list_tools() -> list[dict[str, Any]]:
    """Return defensive copies so a caller cannot mutate future tool listings."""
    return deepcopy(list(_definitions()))


def definition(name: str) -> dict[str, Any]:
    for item in _definitions():
        if item["name"] == name:
            return deepcopy(item)
    raise NormalizationError("UNKNOWN_TOOL")


def _matches(schema: Mapping[str, Any], value: Any) -> bool:
    """Validate the bounded subset used by this tool's committed input schema.

    Unknown validation keywords fail closed. This is not a general JSON Schema
    implementation; conformance tests compare it with Draft202012Validator.
    """
    known = {
        "$schema",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "allOf",
        "anyOf",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "default",
        "description",
        "items",
        "maxItems",
        "minItems",
        "uniqueItems",
        "const",
        "oneOf",
        "not",
        "format",
    }
    if set(schema) - known:
        raise RuntimeError("UNSUPPORTED_INPUT_SCHEMA_KEYWORD")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        if any(not _matches(props[key], v) for key, v in value.items() if key in props):
            return False
    elif kind == "string":
        if not isinstance(value, str):
            return False
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 256):
            return False
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return False
        if schema.get("format") == "date-time":
            if not _rfc3339(value):
                return False
        elif "format" in schema:
            return False
    elif kind == "integer":
        if type(value) is not int:
            return False
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            return False
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if not schema.get("minimum", value) <= value <= schema.get("maximum", value):
            return False
    elif kind == "boolean":
        if type(value) is not bool:
            return False
    elif kind == "array":
        if not isinstance(value, list):
            return False
        if len(value) > schema.get("maxItems", 200):
            return False
        if len(value) < schema.get("minItems", 0):
            return False
        if schema.get("uniqueItems"):
            try:
                fingerprints = [
                    json.dumps(item, sort_keys=True, ensure_ascii=False)
                    for item in value
                ]
            except (TypeError, ValueError):
                return False
            if len(fingerprints) != len(set(fingerprints)):
                return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and any(
            not _matches(item_schema, item) for item in value
        ):
            return False
    elif kind is not None:
        raise RuntimeError("UNSUPPORTED_INPUT_SCHEMA_TYPE")
    # Subschemas containing only required are valid on object instances.
    if (
        "required" in schema
        and isinstance(value, dict)
        and any(key not in value for key in schema["required"])
    ):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "allOf" in schema and not all(_matches(s, value) for s in schema["allOf"]):
        return False
    if "anyOf" in schema and not any(_matches(s, value) for s in schema["anyOf"]):
        return False
    if "not" in schema and _matches(schema["not"], value):
        return False
    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            if _matches(sub, value):
                matched += 1
                if matched > 1:
                    return False
        if matched != 1:
            return False
    return True


def _bounded_argument(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise NormalizationError("INVALID_ARGUMENT")
    if isinstance(value, str):
        if len(value) > 2048:
            raise NormalizationError("INVALID_ARGUMENT")
        return
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > 64:
            raise NormalizationError("INVALID_ARGUMENT")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NormalizationError("INVALID_ARGUMENT")
        return
    if isinstance(value, list):
        if len(value) > 200:
            raise NormalizationError("INVALID_ARGUMENT")
        for item in value:
            _bounded_argument(item, depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32 or any(not isinstance(key, str) for key in value):
            raise NormalizationError("INVALID_ARGUMENT")
        for item in value.values():
            _bounded_argument(item, depth + 1)
        return
    raise NormalizationError("INVALID_ARGUMENT")


def _rfc3339(value: str) -> bool:
    if len(value) > 64 or any(ord(char) < 32 or char.isspace() for char in value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _expected_beans(items: list[dict[str, Any]]) -> tuple[ExpectedBean, ...]:
    return tuple(
        ExpectedBean(
            name=item.get("name"),
            type_name=item.get("type"),
            context_id=item.get("contextId"),
            aliases=tuple(item.get("aliases") or ()),
        )
        for item in items
    )


def _expected_routes(items: list[dict[str, Any]]) -> tuple[ExpectedRoute, ...]:
    return tuple(
        ExpectedRoute(
            path=item["path"],
            http_method=item["httpMethod"],
            controller_class=item.get("controllerClass"),
            context_id=item.get("contextId"),
        )
        for item in items
    )


@overload
def validate_arguments(
    arguments: Mapping[str, Any],
    tool_name: Literal["springboot_inspect_beans"] = "springboot_inspect_beans",
) -> InspectBeansQuery: ...


@overload
def validate_arguments(arguments: Mapping[str, Any], tool_name: str) -> Any: ...


def validate_arguments(
    arguments: Mapping[str, Any],
    tool_name: str = TOOL,
) -> Any:
    if not isinstance(arguments, dict):
        raise NormalizationError("INVALID_ARGUMENT")
    # Bound before serialization, including values that are invalid for the schema.
    if len(arguments) > 12 or any(not isinstance(k, str) for k in arguments):
        raise NormalizationError("INVALID_ARGUMENT")
    for value in arguments.values():
        _bounded_argument(value)
    try:
        encoded_size = len(
            json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (UnicodeError, ValueError, OverflowError):
        raise NormalizationError("INVALID_ARGUMENT") from None
    if encoded_size > 16_384:
        raise NormalizationError("INVALID_ARGUMENT")
    schema = definition(tool_name)["inputSchema"]
    if not _matches(schema, arguments):
        raise NormalizationError("INVALID_ARGUMENT")
    values = {
        key: deepcopy(item["default"])
        for key, item in schema["properties"].items()
        if "default" in item
    }
    values.update(arguments)
    if tool_name == "springboot_diagnose_health":
        return HealthQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            component_path=tuple(values.get("componentPath") or ()),
            group=values["group"],
            include_details=values["includeDetails"],
            include_healthy=values["includeHealthy"],
            max_depth=values["maxDepth"],
        )
    if tool_name == "springboot_verify_mappings":
        return MappingQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            path_pattern=values.get("pathPattern"),
            http_method=values.get("httpMethod"),
            controller_class_pattern=values.get("controllerClassPattern"),
            context_id=values.get("contextId"),
        )
    if tool_name == "springboot_check_migrations":
        expected = tuple(
            ExpectedMigration(
                engine=item["engine"],
                context_id=item["contextId"],
                migration_bean=item["migrationBean"],
                identity=item["identity"],
                version=item.get("version"),
                checksum=item.get("checksum"),
            )
            for item in values.get("expectedMigrations") or ()
        )
        return MigrationQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            engine=values["engine"],
            context_id=values.get("contextId"),
            migration_bean=values.get("migrationBean"),
            states=tuple(values.get("state") or ()),
            version=values.get("version"),
            expected=expected,
            expected_set_complete=values["expectedSetComplete"],
        )
    if tool_name == "springboot_analyze_startup":
        return StartupQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            step_name_pattern=values.get("stepNamePattern"),
            bean_name_pattern=values.get("beanNamePattern"),
            min_duration_ms=values["minDurationMs"],
            max_depth=values["maxDepth"],
        )
    if tool_name == "springboot_inspect_config":
        return ConfigQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            prefix=values.get("prefix"),
            property_key_pattern=values.get("propertyKeyPattern"),
            context_id=values.get("contextId"),
            include_values=values["includeValues"],
        )
    if tool_name == "springboot_detect_drift":
        bean_count = (
            len(arguments["expectedBeans"]) if "expectedBeans" in arguments else 0
        )
        route_count = (
            len(arguments["expectedRoutes"]) if "expectedRoutes" in arguments else 0
        )
        migration_count = 1 if "expectedMigrationVersion" in arguments else 0
        if bean_count + route_count + migration_count > 100:
            raise NormalizationError("INVALID_ARGUMENT")
        migration = arguments.get("expectedMigrationVersion")
        return DriftQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            expected_beans=_expected_beans(arguments["expectedBeans"])
            if "expectedBeans" in arguments
            else None,
            expected_routes=_expected_routes(arguments["expectedRoutes"])
            if "expectedRoutes" in arguments
            else None,
            expected_migration=ExpectedMigrationVersion(
                engine=migration["engine"],
                version=migration["version"],
                context_id=migration.get("contextId"),
                migration_bean=migration.get("migrationBean"),
            )
            if isinstance(migration, dict)
            else None,
            expected_profiles=tuple(arguments["expectedProfiles"])
            if "expectedProfiles" in arguments
            else None,
            expected_scope_complete=values["expectedScopeComplete"],
            source_revision=values.get("sourceRevision"),
            source_modified_at=values.get("sourceModifiedAt"),
            max_depth=values["maxDepth"],
        )
    if tool_name == "springboot_search_openapi":
        return OpenApiQuery(
            target_id=values["targetId"],
            max_results=values["maxResults"],
            cursor=values.get("cursor"),
            path_pattern=values.get("pathPattern"),
            http_method=values.get("httpMethod"),
            operation_id=values.get("operationId"),
            schema_name=values.get("schemaName"),
            max_ref_depth=values["maxRefDepth"],
        )
    if tool_name != TOOL:
        return values
    return InspectBeansQuery(
        target_id=values["targetId"],
        max_results=values["maxResults"],
        cursor=values.get("cursor"),
        bean_name_pattern=values.get("beanNamePattern"),
        type_pattern=values.get("typePattern"),
        package_prefix=values.get("packagePrefix"),
        context_id=values.get("contextId"),
        direction=cast(
            Literal["dependencies", "dependents", "both"], values["direction"]
        ),
        max_depth=values["maxDepth"],
        condition_depth=values["conditionDepth"],
    )


def glob_matches(pattern: str, value: str) -> bool:
    """Linear-space glob DP; user input is never evaluated as a regex."""
    if len(pattern) > 128 or len(value) > 4096:
        return False
    previous = [True] + [False] * len(value)
    for char in pattern:
        current = [False] * (len(value) + 1)
        current[0] = char == "*" and previous[0]
        for index, actual in enumerate(value, 1):
            current[index] = (
                previous[index] or current[index - 1]
                if char == "*"
                else previous[index - 1] and (char == "?" or char == actual)
            )
        previous = current
    return previous[-1]
