"""Local OpenAPI $ref expansion. Remote references are never fetched."""

from __future__ import annotations

from typing import Any

from ..contracts.definitions import glob_matches
from ..models.common import AnalysisBudget, AnalysisLimit
from ..models.queries import OpenApiQuery

_EXPANSION_LIMIT_GAP = "REF_EXPANSION_LIMIT"

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
    """Project local schemas under one shared, per-query analysis budget.

    The budget counts every expansion node across all operations and shared
    references, so a wide fan-out of `$ref`s cannot multiply work per property
    or per reference. Exhaustion yields a bounded placeholder plus an explicit
    gap instead of building the remaining subtrees.
    """

    def __init__(
        self,
        document: dict[str, Any],
        max_depth: int,
        budget: AnalysisBudget | None = None,
    ) -> None:
        self.document = document
        self.max_depth = max_depth
        self.budget = AnalysisBudget() if budget is None else budget
        self.gaps: set[str] = set()
        self.blocked_name: str | None = None
        self._exhausted = False

    def _consume(self) -> bool:
        """Charge one expansion visit; False once the shared budget is spent."""
        if self._exhausted:
            return False
        try:
            self.budget.consume()
        except AnalysisLimit:
            self._exhausted = True
            self.gaps.add(_EXPANSION_LIMIT_GAP)
            return False
        return True

    def expand(
        self, schema: Any, depth: int, seen: frozenset[str]
    ) -> tuple[dict[str, Any], bool]:
        if not self._consume():
            return {
                "referenceState": "unsupported",
                "omissionCodes": [_EXPANSION_LIMIT_GAP],
            }, False
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
        projected, complete = self.expand(target, depth + 1, seen | {ref})
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
                if self._exhausted:
                    return {
                        "referenceState": "unsupported",
                        "omissionCodes": [_EXPANSION_LIMIT_GAP],
                    }, False
                kept[name] = child
                complete = complete and child_complete
                if len(kept) >= 50:
                    complete = False
                    break
            projected["properties"] = kept
        if "items" in schema:
            child, child_complete = self.expand(schema["items"], depth, seen)
            if self._exhausted:
                return {
                    "referenceState": "unsupported",
                    "omissionCodes": [_EXPANSION_LIMIT_GAP],
                }, False
            projected["items"] = child
            complete = complete and child_complete
        return projected, complete


def _object(value: Any, expander: _Expander) -> dict[str, Any] | None:
    """Resolve bounded local Parameter/RequestBody references, never URLs."""
    seen: set[str] = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            expander.gaps.add("REF_EXTERNAL_BLOCKED")
            return None
        if ref in seen:
            expander.gaps.add("REF_CYCLE")
            return None
        if len(seen) >= expander.max_depth or not expander._consume():
            expander.gaps.add(
                "REF_DEPTH_LIMIT" if not expander._exhausted else _EXPANSION_LIMIT_GAP
            )
            return None
        seen.add(ref)
        value = _pointer(expander.document, ref)
    if not isinstance(value, dict):
        expander.gaps.add("OPENAPI_OBJECT_UNREADABLE")
        return None
    return value


def _parameters(
    operation: dict[str, Any], expander: _Expander, inherited: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = operation.get("parameters", [])
    if not isinstance(raw, list) or not isinstance(inherited, list):
        expander.gaps.add("OPENAPI_PARAMETERS_UNREADABLE")
        return rows
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for value in [*inherited, *raw]:
        item = _object(value, expander)
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            expander.gaps.add("OPENAPI_PARAMETERS_UNREADABLE")
            continue
        location = item.get("in")
        if location not in {"path", "query", "header", "cookie"}:
            expander.gaps.add("OPENAPI_PARAMETERS_UNREADABLE")
            continue
        selected[(location, item["name"])] = item
    if len(selected) > 16:
        expander.gaps.add("OPENAPI_PARAMETER_LIMIT")
    for (location, name), item in sorted(selected.items())[:16]:
        schema, _ = expander.expand(item.get("schema"), 0, frozenset())
        rows.append(
            {
                "name": name[:128],
                "in": location,
                "required": item.get("required") is True,
                "schema": schema,
            }
        )
    return rows


def _request_body(
    operation: dict[str, Any], expander: _Expander
) -> list[dict[str, Any]]:
    if "requestBody" not in operation:
        return []
    body = _object(operation["requestBody"], expander)
    content = body.get("content") if body is not None else None
    if not isinstance(content, dict) or not content:
        expander.gaps.add("OPENAPI_REQUEST_BODY_UNREADABLE")
        return []
    if len(content) > 8:
        expander.gaps.add("OPENAPI_MEDIA_LIMIT")
    rows = []
    for media in sorted(content)[:8]:
        entry = content[media]
        schema, _ = expander.expand(
            entry.get("schema") if isinstance(entry, dict) else None, 0, frozenset()
        )
        rows.append({"mediaType": media[:128], "schema": schema})
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
            parameters = _parameters(
                operation, expander, operations.get("parameters", [])
            )
            request_body = _request_body(operation, expander)
            items.append(
                {
                    "kind": "operation",
                    "path": path[:1024],
                    "httpMethod": http_method,
                    "operationId": operation_id
                    if isinstance(operation_id, str)
                    else None,
                    "schemaName": None,
                    "parameters": parameters,
                    "requestBody": request_body,
                    "responses": responses,
                    "schema": None,
                    "expansionComplete": complete and not expander.gaps,
                }
            )
    for index, item in enumerate(items, start=1):
        item["id"] = f"openapi_{index:06d}"
    return version, "default", items, sorted(expander.gaps), expander.blocked_name
