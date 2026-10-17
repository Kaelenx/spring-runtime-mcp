"""Independent rejection gate for cache and wire sinks; never repair raw JSON."""

from __future__ import annotations

from typing import Any

from ..models.common import NormalizationError
from ..transport.bounded_json import loads_bounded, validate_tree
from .sanitization import MARKER, SanitizerPipeline, sensitive_key

DIAGNOSTIC_MESSAGE = "Diagnostic evidence is unavailable; review the registered target and access policy."
REVIEW_TARGET = (
    "Review target registration, endpoint exposure and authorization locally."
)
REVIEW_AUTHORIZATION = "Review the existing Spring Security rule and diagnostic credentials; do not disable security or follow login redirects."
REVIEW_STARTUP = "Review startup endpoint exposure; if collection is absent, configure SpringApplication.setApplicationStartup(new BufferingApplicationStartup(2048)) before run; restart requires approval."
LOCAL_PROSE = frozenset(
    {
        DIAGNOSTIC_MESSAGE,
        REVIEW_TARGET,
        REVIEW_AUTHORIZATION,
        REVIEW_STARTUP,
        "registered target Bean contexts",
        "registered target health",
        "registered target mappings",
        "registered target migrations",
        "registered target startup",
        "registered target config",
        "registered target openapi",
    }
)
OMITTED_TEXT_FIELDS = frozenset(
    {"description", "comments", "stackTrace", "instructions", "system", "role"}
)


def ensure_safe_output(
    value: Any, *, sanitizer: SanitizerPipeline | None = None
) -> None:
    """Reject recognizable secrets, raw prose and unsafe secondary JSON at sinks.

    This complements typed projection. Opaque unknown secrets have no universal
    recognizer; producers must still allowlist facts and register credentials.
    """
    validate_tree(value, max_bytes=32 * 1024 * 1024)
    pipeline = sanitizer or SanitizerPipeline()
    pending = [(value, "", 0)]
    while pending:
        item, field, depth = pending.pop()
        if depth > 64:
            raise NormalizationError("UNSAFE_OUTPUT")
        if isinstance(item, dict):
            for key, child in item.items():
                if pipeline.high_risk(key) or (
                    sensitive_key(key) and child not in (MARKER, None)
                ):
                    raise NormalizationError("UNSAFE_OUTPUT")
                if key in OMITTED_TEXT_FIELDS and child not in (MARKER, None, [], {}):
                    raise NormalizationError("UNTRUSTED_OUTPUT")
                pending.append((child, key, depth + 1))
        elif isinstance(item, list):
            pending.extend((child, field, depth + 1) for child in item)
        elif isinstance(item, str) and item != MARKER:
            if pipeline.high_risk(item):
                raise NormalizationError("UNSAFE_OUTPUT")
            if (
                any(c.isspace() for c in item)
                and item not in LOCAL_PROSE
                and not item.lstrip().startswith(("{", "["))
            ):
                raise NormalizationError("UNTRUSTED_OUTPUT")
            if field in ("message", "steps") and item not in LOCAL_PROSE:
                raise NormalizationError("UNTRUSTED_OUTPUT")
            if field == "nextAction":
                raise NormalizationError("UNTRUSTED_OUTPUT")
            if item.lstrip().startswith(("{", "[")):
                try:
                    nested = loads_bounded(
                        ('{"value":' + item.strip() + "}").encode(),
                        max_bytes=65_550,
                        max_depth=32,
                        max_nodes=10_000,
                    )["value"]
                except NormalizationError:
                    raise NormalizationError("UNSAFE_OUTPUT") from None
                pending.append((nested, field, depth + 1))
