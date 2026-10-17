"""Bounded in-memory cursor store; callers supply sanitized envelopes only."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from .models.common import NormalizationError
from .models.queries import InspectBeansQuery
from .policy.output_guard import ensure_safe_output
from .policy.sanitization import SanitizerPipeline


@dataclass(frozen=True)
class _Entry:
    data: bytes
    query_digest: bytes
    expires_at: float


class SnapshotStore:
    """Opaque cursor handles bind a frozen result to all non-cursor arguments."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        ttl: float = 60.0,
        max_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if not 0 < ttl <= 60 or not 0 < max_bytes <= 32 * 1024 * 1024:
            raise ValueError("INVALID_SNAPSHOT_LIMITS")
        self._clock = clock
        self._ttl = ttl
        self._max_bytes = max_bytes
        self._size = 0
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    @staticmethod
    def _digest(query: InspectBeansQuery) -> bytes:
        values = asdict(query)
        values.pop("cursor", None)
        encoded = json.dumps(
            values,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).digest()

    def _remove(self, token: str) -> None:
        self._size -= len(self._entries.pop(token).data)

    def _prune(self) -> None:
        now = self._clock()
        for token in tuple(self._entries):
            if self._entries[token].expires_at <= now:
                self._remove(token)

    def put(
        self,
        envelope: dict[str, Any],
        query: InspectBeansQuery,
        *,
        sanitizer: SanitizerPipeline | None = None,
    ) -> str:
        self._prune()
        ensure_safe_output(envelope, sanitizer=sanitizer)
        try:
            data = json.dumps(
                envelope,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise NormalizationError("INVALID_RESPONSE") from None
        if len(data) > self._max_bytes:
            raise NormalizationError("SNAPSHOT_TOO_LARGE")
        while self._entries and self._size + len(data) > self._max_bytes:
            self._remove(next(iter(self._entries)))
        token = secrets.token_urlsafe(32)
        while token in self._entries:
            token = secrets.token_urlsafe(32)
        self._entries[token] = _Entry(
            data, self._digest(query), self._clock() + self._ttl
        )
        self._size += len(data)
        return token

    def get(
        self,
        token: str,
        query: InspectBeansQuery,
        *,
        sanitizer: SanitizerPipeline | None = None,
    ) -> dict[str, Any]:
        self._prune()
        entry = self._entries.get(token)
        if entry is None:
            raise NormalizationError("CURSOR_EXPIRED")
        if not secrets.compare_digest(entry.query_digest, self._digest(query)):
            raise NormalizationError("CURSOR_QUERY_MISMATCH")
        self._entries.move_to_end(token)
        # Decoding produces a fresh mutable copy; callers cannot alter cached facts.
        value: dict[str, Any] = json.loads(entry.data)
        try:
            ensure_safe_output(value, sanitizer=sanitizer)
        except NormalizationError:
            # A newly registered credential/policy may invalidate old facts.
            # Evict rather than retaining a now-recognized secret in the cache.
            self._remove(token)
            raise
        return value
