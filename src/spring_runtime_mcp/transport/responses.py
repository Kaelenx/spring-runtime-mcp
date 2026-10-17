"""Canonical bounded JSON-RPC result framing with compatible text content."""

from __future__ import annotations

import json
from typing import Any

from ..models.common import NormalizationError
from ..policy.output_guard import ensure_safe_output
from ..policy.sanitization import SanitizerPipeline

MAX_RESPONSE_BYTES = 16_384
MAX_SAFE_INTEGER = 2**53 - 1


def serialize_response(
    envelope: dict[str, Any],
    request_id: str | int | None = None,
    *,
    sanitizer: SanitizerPipeline | None = None,
) -> bytes:
    """Measure the entire actual frame, including the duplicated JSON text."""
    if isinstance(request_id, bool) or not isinstance(
        request_id, (str, int, type(None))
    ):
        raise NormalizationError("INVALID_REQUEST_ID")
    if isinstance(request_id, int) and abs(request_id) > MAX_SAFE_INTEGER:
        raise NormalizationError("INVALID_REQUEST_ID")
    try:
        if isinstance(request_id, str) and len(request_id.encode("utf-8")) > 128:
            raise NormalizationError("INVALID_REQUEST_ID")
        text = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        frame = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "resultType": "complete",
                "structuredContent": envelope,
                "content": [{"type": "text", "text": text}],
                "isError": envelope.get("status") not in ("ok", "partial"),
            },
        }
        encoded = json.dumps(
            frame,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, NormalizationError):
            raise
        raise NormalizationError("INVALID_RESPONSE") from None
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise NormalizationError("RESPONSE_TOO_LARGE")
    ensure_safe_output(envelope, sanitizer=sanitizer)
    return encoded
