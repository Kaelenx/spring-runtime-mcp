"""Stdlib GET connector for one registered actuator origin.

This is the only production module allowed to import http.client. It never
follows redirects and never reads more than max_bytes + 1 bytes.
"""

from __future__ import annotations

import asyncio
import http.client
import ssl
from urllib.parse import urlsplit

from .response_gate import MAX_UPSTREAM_BYTES, HttpResponse

_PATHS = {
    "beans": "/actuator/beans",
    "conditions": "/actuator/conditions",
    "health": "/actuator/health",
    "health-liveness": "/actuator/health/liveness",
    "health-readiness": "/actuator/health/readiness",
    "mappings": "/actuator/mappings",
    "flyway": "/actuator/flyway",
    "liquibase": "/actuator/liquibase",
    "startup": "/actuator/startup",
    "openapi": "/actuator/openapi",
    "env": "/actuator/env",
    "configprops": "/actuator/configprops",
}


def _parse_base(url: str) -> tuple[str, str, int]:
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise ValueError("INVALID_BASE_URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise ValueError("INVALID_BASE_URL")
    parts = urlsplit(url)
    host = parts.hostname
    if (
        parts.scheme not in {"http", "https"}
        or host is None
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("INVALID_BASE_URL")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return parts.scheme, host, port


def _read_bounded(response: http.client.HTTPResponse, max_bytes: int) -> bytes:
    remaining = max_bytes + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = response.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class ActuatorHttpTransport:
    """Map a logical endpoint id onto one configured origin. No caller URLs."""

    def __init__(self, base_url: str, *, targets: frozenset[str]) -> None:
        if not targets or any(not isinstance(target, str) for target in targets):
            raise ValueError("INVALID_TARGET_REGISTRATION")
        scheme, host, port = _parse_base(base_url)
        self._scheme = scheme
        self._host = host
        self._port = port
        self._targets = targets

    async def request(
        self,
        target_id: str,
        endpoint_id: str,
        *,
        method: str,
        follow_redirects: bool,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        return await asyncio.to_thread(
            self._exchange,
            target_id,
            endpoint_id,
            method,
            follow_redirects,
            timeout,
            max_bytes,
        )

    def _open(self, timeout: float) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(
                self._host,
                self._port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _exchange(
        self,
        target_id: str,
        endpoint_id: str,
        method: str,
        follow_redirects: bool,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse:
        if method != "GET" or follow_redirects:
            raise AssertionError("READ_ONLY_TRANSPORT")
        if target_id not in self._targets or endpoint_id not in _PATHS:
            raise AssertionError("UNREGISTERED_ENDPOINT")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < float(timeout) <= 60
        ):
            raise AssertionError("INVALID_TIMEOUT")
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 <= max_bytes <= MAX_UPSTREAM_BYTES
        ):
            raise AssertionError("INVALID_MAX_BYTES")
        path = _PATHS[endpoint_id]
        connection = self._open(float(timeout))
        try:
            connection.request(
                method="GET",
                url=path,
                headers={"Accept": "application/json", "Connection": "close"},
            )
            upstream = connection.getresponse()
            body = _read_bounded(upstream, max_bytes)
            content_type = upstream.getheader("Content-Type") or ""
            return HttpResponse(upstream.status, content_type, body)
        finally:
            connection.close()
