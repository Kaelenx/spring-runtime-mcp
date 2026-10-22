"""Fail-closed projection and independent recursive output sanitization.

This pipeline covers the Bean tool's structured data surface; it does not grant
permission to expose arbitrary Actuator maps or free runtime prose.
"""

from __future__ import annotations

import base64
import html
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, unquote

from ..models.common import NormalizationError, remaining_seconds
from ..transport.bounded_json import loads_bounded, validate_tree

MARKER = "[REDACTED_BY_MCP]"
KEY_RE = re.compile(
    r"password|passwd|pwd|secret|token|apikey|accesskey|authorization|credential|"
    r"cookie|sessionid|privatekey|signingkey|passphrase|keystorepassword|truststorepassword",
    re.IGNORECASE | re.ASCII,
)
AUTH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]+", re.IGNORECASE
)
JOSE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*){2,4}(?![A-Za-z0-9_.-])"
)
PEM_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY\b",
    re.IGNORECASE | re.ASCII,
)
URI_RE = re.compile(
    r"jdbc:[a-z][a-z0-9+.-]*:|(?:https?|rediss?|mongodb(?:\+srv)?|amqps?)://|file:/|jar:",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.-]{0,127})[ \t]*[:=][ \t]*")
ENCODED_RE = re.compile(
    r"(?<![A-Za-z0-9_+/-])[A-Za-z0-9_+/-]{16,}={0,2}(?![A-Za-z0-9_+/-])"
)
USERINFO_RE = re.compile(r"(?<![A-Za-z0-9])[^\s:@/]{1,128}:[^\s@/]{1,128}@")
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_.$#/@:+-]*", re.ASCII)
# Only fields in this tool's committed output (plus supported scalar descriptors).
ALLOWED_KEYS = frozenset(
    [
        "schemaVersion",
        "status",
        "source",
        "targetId",
        "snapshotId",
        "observedAt",
        "endpointIds",
        "bootVersion",
        "adapterVersion",
        "policyVersion",
        "data",
        "items",
        "missingBeanAnalysis",
        "totalMatched",
        "truncated",
        "truncationReasons",
        "coverage",
        "state",
        "scope",
        "gaps",
        "warnings",
        "code",
        "recordId",
        "redactions",
        "nextCursor",
        "nextAction",
        "kind",
        "reasonCode",
        "filterSuggestions",
        "parameter",
        "value",
        "errors",
        "endpointId",
        "httpStatus",
        "retryable",
        "message",
        "remediation",
        "recipeId",
        "action",
        "configKeys",
        "steps",
        "requiresApproval",
        "retryAfterMs",
        "id",
        "observationId",
        "contextId",
        "parentContextId",
        "name",
        "type",
        "resource",
        "aliases",
        "producer",
        "configurationClass",
        "methodName",
        "evidenceLevel",
        "dependencies",
        "dependents",
        "fromId",
        "toId",
        "requestedName",
        "targetContextId",
        "targetName",
        "resolution",
        "depth",
        "conditions",
        "conditionType",
        "operator",
        "outcome",
        "childIds",
        "requiredBeanIds",
        "childrenComplete",
        "blockingPaths",
        "origin",
        "propertyKey",
        "key",
        "propertyName",
        "inputs",
        "bindingValue",
        "effectiveValue",
        "overallStatus",
        "activeGroup",
        "componentPath",
        "parentId",
        "details",
        "errorType",
        "remediationCodes",
        "stack",
        "dispatcherName",
        "paths",
        "httpMethods",
        "methodsUnrestricted",
        "controllerClass",
        "handlerMethod",
        "descriptor",
        "handlerKind",
        "matchingComplete",
        "pendingVisibility",
        "engine",
        "migrationBean",
        "identity",
        "script",
        "changeLog",
        "changeSetId",
        "author",
        "version",
        "description",
        "checksum",
        "checksumType",
        "checksumAlgorithm",
        "executionTimeMs",
        "orderExecuted",
        "executedAt",
        "rawState",
        "stateEvidence",
        "attentionRequired",
        "negated",
        "prefix",
        "file",
        "line",
        "column",
        "environmentVariable",
        "activeProfiles",
        "springBootVersion",
        "totalDurationMs",
        "durationBasis",
        "timelineComplete",
        "stepId",
        "parentStepId",
        "startedAt",
        "durationNanos",
        "durationMs",
        "tags",
        "ancestorIds",
        "relationState",
        "bottleneckCode",
        "effectiveValue",
        "valueState",
        "bindingValue",
        "bindingState",
        "configurationBean",
        "sourceKind",
        "effective",
        "openapiVersion",
        "candidateId",
        "parameters",
        "requestBody",
        "responses",
        "schema",
        "expansionComplete",
        "mediaType",
        "in",
        "httpMethod",
        "operationId",
        "schemaName",
        "path",
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
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "allOf",
        "oneOf",
        "anyOf",
        "$ref",
        "referenceState",
        "omissionCodes",
        "enum",
        "missingInRuntime",
        "unexpectedInRuntime",
        "profileMismatch",
        "rootCauseInferences",
        "dimension",
        "expected",
        "actual",
        "affectedDependencyIds",
        "confidence",
        "evidenceIds",
    ]
)


def sensitive_key(key: str) -> bool:
    view = unicodedata.normalize("NFKC", key).casefold()
    view = re.sub(r"[^a-z0-9]", "", view, flags=re.ASCII)
    return KEY_RE.search(view) is not None


class SanitizerPipeline:
    def __init__(self, known_secrets: tuple[str, ...] = ()) -> None:
        variants: set[str] = set()
        for secret in known_secrets:
            if not secret or len(secret.encode("utf-8")) > 8192:
                raise ValueError("INVALID_SECRET_CONFIGURATION")
            variants.update(
                (
                    secret,
                    quote(secret, safe=""),
                    json.dumps(secret)[1:-1],
                    base64.b64encode(secret.encode()).decode("ascii"),
                    base64.b64encode(secret.encode()).decode("ascii").rstrip("="),
                    base64.urlsafe_b64encode(secret.encode())
                    .decode("ascii")
                    .rstrip("="),
                )
            )
        if len(variants) > 32:
            raise ValueError("SECRET_VARIANT_LIMIT")
        self._secrets = tuple(sorted(variants))

    def high_risk(self, value: str, *, _encoding_depth: int = 0) -> bool:
        remaining_seconds()
        if value == MARKER:
            return False
        if len(value) > 65_536:
            return True
        try:
            if len(value.encode("utf-8")) > 65_536:
                return True
        except UnicodeError:
            return True
        views = [value]
        for _ in range(4):
            decoded = unquote(views[-1])
            if decoded == views[-1]:
                break
            views.append(decoded)
        if re.search(r"%[0-9A-Fa-f]{2}", views[-1]) or re.search(
            r"\\(?:u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|x[0-9A-Fa-f]{2})", value
        ):
            return True
        views += [html.unescape(view) for view in tuple(views)]
        # NFKC also exposes full-width keys, NBSP separators and zero-width
        # formatting used to disguise an otherwise recognizable credential.
        views = list(
            dict.fromkeys(
                views
                + [
                    "".join(
                        c
                        for c in unicodedata.normalize("NFKC", view)
                        if unicodedata.category(c) != "Cf"
                    )
                    for view in views
                ]
            )
        )
        for view in views:
            if any(secret in view for secret in self._secrets):
                return True
            if (
                AUTH_RE.search(view)
                or PEM_RE.search(view)
                or URI_RE.search(view)
                or USERINFO_RE.search(view)
            ):
                return True
            if any(sensitive_key(m.group(1)) for m in ASSIGNMENT_RE.finditer(view)):
                return True
            for candidate in ENCODED_RE.finditer(view):
                text = candidate.group()
                if len(text) > 8192:
                    return True
                try:
                    raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
                except ValueError:
                    continue
                try:
                    decoded_text = raw.decode("utf-8")
                except UnicodeError:
                    decoded_text = raw.decode("latin-1")
                if _encoding_depth >= 2:
                    return True
                if self.high_risk(decoded_text, _encoding_depth=_encoding_depth + 1):
                    return True
            for candidate in JOSE_RE.finditer(view):
                parts = candidate.group().split(".")
                if len(parts) not in (3, 5):
                    continue
                first = parts[0]
                if len(first) > 4096:
                    return True
                try:
                    header = loads_bounded(
                        base64.urlsafe_b64decode(first + "=" * (-len(first) % 4)),
                        max_bytes=4096,
                        max_depth=8,
                        max_nodes=128,
                    )
                except (ValueError, NormalizationError):
                    for part in parts:
                        try:
                            segment_raw = base64.urlsafe_b64decode(
                                part + "=" * (-len(part) % 4)
                            )
                        except ValueError:
                            continue
                        try:
                            segment = segment_raw.decode("utf-8")
                        except UnicodeError:
                            segment = segment_raw.decode("latin-1")
                        if self.high_risk(segment, _encoding_depth=_encoding_depth + 1):
                            return True
                    continue
                if any(key in header for key in ("alg", "enc", "typ")):
                    return True
        return False

    def identifier(self, value: str | None, *, max_length: int = 256) -> str | None:
        if value is None:
            return None
        if value == MARKER or self.high_risk(value) or sensitive_key(value):
            return MARKER
        if len(value) > max_length or IDENTIFIER_RE.fullmatch(value) is None:
            return MARKER
        return value

    def resource(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self.high_risk(value):
            return MARKER
        if value == MARKER:
            return MARKER
        match = re.fullmatch(r"class path resource \[([A-Za-z0-9_$/.-]+)\]", value)
        if match is not None:
            relative = match.group(1)
        elif value.startswith("classpath:"):
            relative = value[len("classpath:") :]
            if re.fullmatch(r"[A-Za-z0-9_$/.-]+", relative) is None:
                return None
        else:
            return None
        if (
            relative.startswith("/")
            or ".." in relative.split("/")
            or relative in ("", ".")
            or len(relative) > 240
        ):
            return None
        return self.identifier("classpath:" + relative)

    def sanitize(self, value: Mapping[str, Any]) -> dict[str, Any]:
        validate_tree(dict(value), max_bytes=32 * 1024 * 1024)
        counter = [0]

        identifier_fields = frozenset(
            (
                "targetId",
                "snapshotId",
                "observationId",
                "id",
                "contextId",
                "parentContextId",
                "name",
                "type",
                "configurationClass",
                "methodName",
                "requestedName",
                "targetContextId",
                "targetName",
                "conditionType",
                "recordId",
                "propertyKey",
                "propertyName",
                "key",
            )
        )
        identifier_arrays = frozenset(("aliases", "childIds", "requiredBeanIds"))

        def walk(item: Any, depth: int = 0, embedded: int = 0, field: str = "") -> Any:
            counter[0] += 1
            if depth > 64 or counter[0] > 100_000:
                raise NormalizationError("SANITIZATION_FAILED")
            if isinstance(item, dict):
                descriptor_tainted = any(
                    isinstance(item.get(key), str) and sensitive_key(item[key])
                    for key in ("name", "key", "propertyKey", "propertyName")
                )
                result: dict[str, Any] = {}
                for key, child in item.items():
                    if field == "properties":
                        if (
                            not isinstance(key, str)
                            or not key
                            or len(key) > 128
                            or any(char.isspace() or ord(char) < 32 for char in key)
                            or sensitive_key(key)
                            or self.high_risk(key)
                        ):
                            continue
                    elif key not in ALLOWED_KEYS or key == "redactions":
                        continue
                    # Migration descriptions are required by schema and are
                    # never a channel for upstream prose or credentials.
                    if key == "description":
                        result[key] = None
                        continue
                    if sensitive_key(key) or (
                        descriptor_tainted
                        and key
                        in (
                            "value",
                            "origin",
                            "inputs",
                            "effectiveValue",
                            "bindingValue",
                        )
                    ):
                        if "propertyKey" in item and key == "origin":
                            result[key] = {
                                "kind": "unknown",
                                "file": None,
                                "line": None,
                                "column": None,
                                "environmentVariable": None,
                            }
                        elif "propertyKey" in item and child is None:
                            result[key] = None
                        else:
                            result[key] = MARKER
                    else:
                        result[key] = walk(child, depth + 1, embedded, key)
                if "propertyKey" in item and descriptor_tainted:
                    for value_key, state_key in (
                        ("effectiveValue", "valueState"),
                        ("bindingValue", "bindingState"),
                    ):
                        if result.get(value_key) == MARKER:
                            result[state_key] = "redacted"
                return result
            if isinstance(item, list):
                return [walk(child, depth + 1, embedded, field) for child in item]
            if isinstance(item, str):
                if item == MARKER:
                    return MARKER
                if field == "message":
                    from .output_guard import LOCAL_PROSE

                    return item if item in LOCAL_PROSE else MARKER
                if field == "nextAction":
                    return MARKER
                if field == "scope":
                    from .output_guard import LOCAL_PROSE

                    return item if item in LOCAL_PROSE else self.identifier(item)
                if field in identifier_fields or field in identifier_arrays:
                    return self.identifier(item)
                if field == "resource":
                    return self.resource(item)
                if field == "origin":
                    # Arbitrary file locations or prose are not a public fact.
                    return MARKER if self.high_risk(item) else None
                stripped = item.strip()
                if stripped.startswith(("{", "[")):
                    if embedded >= 4 or len(item) > 65_536:
                        return MARKER
                    try:
                        nested = loads_bounded(
                            ('{"value":' + stripped + "}").encode(),
                            max_bytes=65_550,
                            max_depth=32,
                            max_nodes=10_000,
                        )["value"]
                        return json.dumps(
                            walk(nested, depth + 1, embedded + 1),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    except (ValueError, NormalizationError):
                        return MARKER
                if self.high_risk(item) or any(ord(c) < 32 for c in item):
                    return MARKER
                if any(c.isspace() for c in item):
                    from .output_guard import LOCAL_PROSE

                    if item not in LOCAL_PROSE:
                        return MARKER
            return item

        result = walk(dict(value))
        if not isinstance(result, dict):
            raise NormalizationError("SANITIZATION_FAILED")
        paths: list[str] = []

        def markers(item: Any, path: str = "") -> None:
            if item == MARKER and isinstance(item, str):
                if len(paths) < 50:
                    paths.append(path)
            elif isinstance(item, dict):
                for key, child in item.items():
                    markers(child, path + "/" + key)
            elif isinstance(item, list):
                for index, child in enumerate(item):
                    markers(child, path + "/" + str(index))

        markers(result)
        if "schemaVersion" in result:
            result["redactions"] = sorted(set(paths))
        return result
