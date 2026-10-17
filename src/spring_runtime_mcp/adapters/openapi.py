"""Local OpenAPI $ref expansion. Remote references are never fetched."""

from __future__ import annotations

from typing import Any

from ..contracts.definitions import glob_matches
from ..models.queries import OpenApiQuery

_METHODS = {
    "get",
    "head",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "trace",
}
_SCHEMA_KEYS = {
    "type",
    "format",
    "nullable",
    "readOnly",
    "writeOnly",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "required",
    "enum",
}


def _pointer(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node: Any = document
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _schema_name(ref: str) -> str | None:
    marker = "/components/schemas/"
    if marker not in ref:
        return None
    return ref.rsplit("/", 1)[-1] or None


class _Expander:
    def __init__(self, document: dict[str, Any], max_depth: int) -> None:
        self.document = document
        self.max_depth = max_depth
        self.gaps: set[str] = set()
        self.blocked_name: str | None = None

    def expand(
        self, schema: Any, depth: int, seen: frozenset[str]
    ) -> tuple[dict[str, Any], bool]:
        if not isinstance(schema, dict):
            self.gaps.add("OPENAPI_SCHEMA_UNREADABLE")
            return {"referenceState": "invalid"}, False
        ref = schema.get("$ref")
        if isinstance(ref, str):
            return self._resolve(ref, depth, seen)
        return self._project(schema, depth, seen)

    def _resolve(
        self, ref: str, depth: int, seen: frozenset[str]
    ) -> tuple[dict[str, Any], bool]:
        if not ref.startswith("#"):
            self.gaps.add("REF_EXTERNAL_BLOCKED")
            return {"referenceState": "externalBlocked"}, False
        if ref in seen:
            self.gaps.add("REF_CYCLE")
            return {"$ref": ref, "referenceState": "cycle"}, False
        if depth >= self.max_depth:
            self.gaps.add("REF_DEPTH_LIMIT")
            self.blocked_name = self.blocked_name or _schema_name(ref)
            target = _pointer(self.document, ref)
            kind = target.get("type") if isinstance(target, dict) else None
            return {
                "type": kind if isinstance(kind, str) else "object",
                "referenceState": "depthLimit",
            }, False
        target = _pointer(self.document, ref)
        if not isinstance(target, dict):
            self.gaps.add("REF_INVALID")
            return {"$ref": ref, "referenceState": "invalid"}, False
        projected, complete = self._project(target, depth + 1, seen | {ref})
        projected["$ref"] = ref
        projected["referenceState"] = "resolved"
        return projected, complete

    def _project(
        self, schema: dict[str, Any], depth: int, seen: frozenset[str]
    ) -> tuple[dict[str, Any], bool]:
        projected: dict[str, Any] = {}
        complete = True
        for key in _SCHEMA_KEYS:
            if key not in schema:
                continue
            value = schema[key]
            if isinstance(value, str) and any(char.isspace() for char in value):
                complete = False
                continue
            projected[key] = value
        properties = schema.get("properties")
        if isinstance(properties, dict):
            kept: dict[str, Any] = {}
            for name in sorted(properties):
                if (
                    not isinstance(name, str)
                    or len(name) > 128
                    or any(char.isspace() for char in name)
                ):
                    complete = False
                    continue
                child, child_complete = self.expand(properties[name], depth, seen)
                kept[name] = child
                complete = complete and child_complete
                if len(kept) >= 50:
                    complete = False
                    break
            projected["properties"] = kept
        if "items" in schema:
            child, child_complete = self.expand(schema["items"], depth, seen)
            projected["items"] = child
            complete = complete and child_complete
        return projected, complete


def _parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = operation.get("parameters", [])
    if not isinstance(raw, list):
        return rows
    for item in raw[:16]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        location = item.get("in")
        if location not in {"path", "query", "header", "cookie"}:
            continue
        rows.append(
            {
                "name": item["name"][:128],
                "in": location,
                "required": bool(item.get("required", False)),
                "schema": {"type": "string"},
            }
        )
    return rows


def normalize_openapi(
    body: Any, query: OpenApiQuery
) -> tuple[str | None, str, list[dict[str, Any]], list[str], str | None]:
    if not isinstance(body, dict) or not isinstance(body.get("paths"), dict):
        raise ValueError("INVALID_OPENAPI_SHAPE")
    version = (
        body.get("openapi")
        if isinstance(body.get("openapi"), str)
        else body.get("swagger")
    )
    version = version if isinstance(version, str) else None
    expander = _Expander(body, query.max_ref_depth)
    items: list[dict[str, Any]] = []
    if query.schema_name:
        schemas = (
            body.get("components", {}).get("schemas", {})
            if isinstance(body.get("components"), dict)
            else {}
        )
        schema = schemas.get(query.schema_name) if isinstance(schemas, dict) else None
        if isinstance(schema, dict):
            projected, complete = expander.expand(schema, 0, frozenset())
            items.append(
                {
                    "id": "openapi_000001",
                    "kind": "schema",
                    "path": None,
                    "httpMethod": None,
                    "operationId": None,
                    "schemaName": query.schema_name,
                    "parameters": [],
                    "requestBody": [],
                    "responses": [],
                    "schema": projected,
                    "expansionComplete": complete,
                }
            )
        return version, "default", items, sorted(expander.gaps), expander.blocked_name
    paths = body["paths"]
    for path in sorted(paths):
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        if query.path_pattern and not glob_matches(query.path_pattern, path):
            continue
        operations = paths[path]
        if not isinstance(operations, dict):
            continue
        for method in sorted(operations):
            if method not in _METHODS:
                continue
            http_method = method.upper()
            if query.http_method and http_method != query.http_method:
                continue
            operation = operations[method]
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if query.operation_id and operation_id != query.operation_id:
                continue
            responses: list[dict[str, Any]] = []
            complete = True
            raw_responses = operation.get("responses")
            if isinstance(raw_responses, dict):
                for status in sorted(raw_responses):
                    if len(responses) >= 16:
                        complete = False
                        break
                    entry = raw_responses[status]
                    content = entry.get("content") if isinstance(entry, dict) else None
                    if not isinstance(content, dict) or not content:
                        responses.append(
                            {
                                "status": str(status)[:16],
                                "mediaType": None,
                                "schema": None,
                            }
                        )
                        continue
                    media = min(content)
                    schema = (
                        content[media].get("schema")
                        if isinstance(content[media], dict)
                        else None
                    )
                    projected, schema_complete = expander.expand(schema, 0, frozenset())
                    complete = complete and schema_complete
                    responses.append(
                        {
                            "status": str(status)[:16],
                            "mediaType": media if isinstance(media, str) else None,
                            "schema": projected,
                        }
                    )
            else:
                complete = False
            items.append(
                {
                    "kind": "operation",
                    "path": path[:1024],
                    "httpMethod": http_method,
                    "operationId": operation_id
                    if isinstance(operation_id, str)
                    else None,
                    "schemaName": None,
                    "parameters": _parameters(operation),
                    "requestBody": [],
                    "responses": responses,
                    "schema": None,
                    "expansionComplete": complete and not expander.gaps,
                }
            )
    for index, item in enumerate(items, start=1):
        item["id"] = f"openapi_{index:06d}"
    return version, "default", items, sorted(expander.gaps), expander.blocked_name
