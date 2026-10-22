"""Stdlib GET connector for one registered actuator origin.

This is the only production module allowed to import http.client. It never
follows redirects and never reads more than max_bytes + 1 bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .response_gate import MAX_UPSTREAM_BYTES, HttpResponse
from ..models.common import NormalizationError

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
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
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
    if not 1 <= port <= 65535 or "%" in host:
        raise ValueError("INVALID_BASE_URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if re.fullmatch(
            r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*", host
        ):
            raise ValueError("INVALID_BASE_URL") from None
        host = host.removesuffix(".").encode("idna").decode("ascii").lower()
        if len(host) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in host.split(".")
        ):
            raise ValueError("INVALID_BASE_URL")
    else:
        _loopback(str(address))
        host = str(address)
    return parts.scheme, host, port


def _loopback(host: str) -> str:
    address = ipaddress.ip_address(host)
    effective = (
        address.ipv4_mapped
        if isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        else address
    )
    if not (
        effective in ipaddress.ip_network("127.0.0.0/8")
        if effective.version == 4
        else effective == ipaddress.ip_address("::1")
    ):
        raise NormalizationError("ADDRESS_DENIED")
    return str(effective)


class _ExchangeState:
    """Own the real socket even when HTTPResponse detaches it from the client."""

    def __init__(self, timeout: float) -> None:
        self.deadline = time.monotonic() + timeout
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.connection: http.client.HTTPConnection | None = None
        self.sock: socket.socket | None = None

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise TimeoutError()
        return remaining

    def bind(self, connection: http.client.HTTPConnection) -> None:
        with self.lock:
            self.connection = connection
        if self.cancelled.is_set():
            connection.close()
            raise TimeoutError()

    def bind_socket(self, sock: socket.socket) -> None:
        with self.lock:
            self.sock = sock
        if self.cancelled.is_set():
            sock.close()
            raise TimeoutError()

    def abort(self) -> None:
        self.cancelled.set()
        with self.lock:
            connection, sock = self.connection, self.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        if connection is not None:
            connection.close()


class _VerifiedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, port: int, timeout: float, state: _ExchangeState
    ) -> None:
        self._tls_context = ssl.create_default_context()
        self._state = state
        super().__init__(host, port, timeout=timeout, context=self._tls_context)

    def connect(self) -> None:
        # The injected connector pins and verifies the raw peer before TLS.
        http.client.HTTPConnection.connect(self)
        assert self.sock is not None
        wrapped = self._tls_context.wrap_socket(
            self.sock, server_hostname=self.host, do_handshake_on_connect=False
        )
        self.sock = wrapped
        self._state.bind_socket(wrapped)
        wrapped.settimeout(self._state.remaining())
        wrapped.do_handshake()


def _read_bounded(
    response: http.client.HTTPResponse, max_bytes: int, state: _ExchangeState
) -> bytes:
    remaining = max_bytes + 1
    chunks: list[bytes] = []
    while remaining:
        state.remaining()
        chunk = response.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class ActuatorHttpTransport:
    """Map a logical endpoint id onto one configured origin. No caller URLs."""

    def __init__(
        self, base_url: str, *, targets: frozenset[str], bearer_token: str | None = None
    ) -> None:
        if not targets or any(not isinstance(target, str) for target in targets):
            raise ValueError("INVALID_TARGET_REGISTRATION")
        scheme, host, port = _parse_base(base_url)
        self._scheme = scheme
        self._host = host
        self._port = port
        self._targets = targets
        if bearer_token is not None and (
            not isinstance(bearer_token, str)
            or not 1 <= len(bearer_token) <= 8192
            or re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", bearer_token) is None
        ):
            raise ValueError("INVALID_CREDENTIAL_REGISTRATION")
        self._bearer_token = bearer_token

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
        state = _ExchangeState(float(timeout))
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._exchange,
                target_id,
                endpoint_id,
                method,
                follow_redirects,
                timeout,
                max_bytes,
                state,
            )
        )
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker), timeout=float(timeout)
            )
        except (TimeoutError, asyncio.CancelledError):
            state.abort()
            # Socket shutdown wakes a blocked body read; always collect worker
            # exceptions. A resolver syscall itself is owned by the OS.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(worker), timeout=0.25)
            if not worker.done():
                worker.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
            raise
        except ssl.SSLError:
            raise NormalizationError("TLS_VERIFICATION_FAILED") from None

    def _open(
        self, timeout: float, state: _ExchangeState
    ) -> http.client.HTTPConnection:
        if self._scheme == "https":
            connection: http.client.HTTPConnection = _VerifiedHTTPSConnection(
                self._host, self._port, timeout, state
            )
        else:
            connection = http.client.HTTPConnection(
                self._host, self._port, timeout=timeout
            )

        def connect(
            address: Any, timeout: Any = None, source_address: Any = None
        ) -> socket.socket:
            if source_address is not None or address != (self._host, self._port):
                raise NormalizationError("ADDRESS_DENIED")
            records: list[tuple[socket.AddressFamily, socket.SocketKind, int, str, Any]]
            try:
                literal = ipaddress.ip_address(self._host)
            except ValueError:
                records = socket.getaddrinfo(
                    self._host, self._port, type=socket.SOCK_STREAM
                )
            else:
                family = socket.AF_INET if literal.version == 4 else socket.AF_INET6
                records = [
                    (family, socket.SOCK_STREAM, 6, "", (str(literal), self._port))
                ]
            if not records or len(records) > 16:
                raise NormalizationError("ADDRESS_DENIED")
            for family, kind, _, _, endpoint in records:
                resolved_host = endpoint[0]
                if (
                    family not in (socket.AF_INET, socket.AF_INET6)
                    or kind != socket.SOCK_STREAM
                    or endpoint[1] != self._port
                    or not isinstance(resolved_host, str)
                    or "%" in resolved_host
                ):
                    raise NormalizationError("ADDRESS_DENIED")
                _loopback(resolved_host)
            for index, (family, _, _, _, endpoint) in enumerate(records):
                sock = socket.socket(family, socket.SOCK_STREAM)
                state.bind_socket(sock)
                try:
                    sock.settimeout(state.remaining())
                    sock.connect(endpoint)
                    peer = sock.getpeername()
                    if (
                        _loopback(peer[0]) != _loopback(endpoint[0])
                        or peer[1] != self._port
                    ):
                        raise NormalizationError("PEER_MISMATCH")
                    state.remaining()
                    return sock
                except OSError:
                    sock.close()
                    if index == len(records) - 1:
                        raise
                except BaseException:
                    sock.close()
                    raise
            raise NormalizationError("ADDRESS_DENIED")

        # http.client's socket factory is the narrow seam that preserves its
        # framing while preventing a second hostname lookup or proxy handling.
        setattr(connection, "_create_connection", connect)
        connection.set_debuglevel(0)
        return connection

    def _exchange(
        self,
        target_id: str,
        endpoint_id: str,
        method: str,
        follow_redirects: bool,
        timeout: float,
        max_bytes: int,
        state: _ExchangeState,
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
        connection = self._open(float(timeout), state)
        state.bind(connection)
        try:
            state.remaining()
            headers = {"Accept": "application/json", "Connection": "close"}
            if self._bearer_token is not None:
                headers["Authorization"] = "Bearer " + self._bearer_token
            connection.request(
                method="GET",
                url=path,
                headers=headers,
            )
            upstream = connection.getresponse()
            body = _read_bounded(upstream, max_bytes, state)
            content_type = upstream.getheader("Content-Type") or ""
            return HttpResponse(upstream.status, content_type, body)
        finally:
            connection.close()
