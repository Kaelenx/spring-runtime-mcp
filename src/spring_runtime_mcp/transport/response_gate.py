"""Read-only request/response policy for an injected, registered-target transport.

This gate does not open sockets. The production connector lives in
actuator_http.py and must keep GET-only, non-following, bounded reads.
"""

from __future__ import annotations

import asyncio
import errno
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from ..models.common import NormalizationError, ParsedObservation
from .bounded_json import loads_bounded

MAX_UPSTREAM_BYTES = 8 * 1024 * 1024
ENDPOINTS = frozenset(
    {
        "beans",
        "conditions",
        "health",
        "mappings",
        "flyway",
        "liquibase",
        "startup",
        "openapi",
        "health-liveness",
        "health-readiness",
        "env",
        "configprops",
    }
)
HEALTH_ENDPOINTS = frozenset({"health", "health-liveness", "health-readiness"})


class EndpointReadError(NormalizationError):
    def __init__(
        self, code: str, endpoint_id: str, http_status: int | None = None
    ) -> None:
        super().__init__(code)
        self.endpoint_id = endpoint_id
        self.http_status = http_status


@dataclass(frozen=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes


class RegisteredTransport(Protocol):
    async def request(
        self,
        target_id: str,
        endpoint_id: str,
        *,
        method: str,
        follow_redirects: bool,
        timeout: float,
        max_bytes: int,
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class ResponseGateReader:
    """Frozen logical capabilities; the transport is a trusted dependency."""

    transport: RegisteredTransport
    targets: frozenset[str]
    enabled_endpoints: frozenset[str]
    timeout: float = 5.0

    def __post_init__(self) -> None:
        targets = frozenset(self.targets)
        endpoints = frozenset(self.enabled_endpoints)
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)):
            raise ValueError("INVALID_TIMEOUT")
        if not 0 < float(self.timeout) <= 60:
            raise ValueError("INVALID_TIMEOUT")
        if not targets or any(
            not isinstance(target, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target) is None
            for target in targets
        ):
            raise ValueError("INVALID_TARGET_REGISTRATION")
        if not endpoints <= ENDPOINTS:
            raise ValueError("UNREGISTERED_ENDPOINT")
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "enabled_endpoints", endpoints)
        object.__setattr__(self, "timeout", float(self.timeout))

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        if target_id not in self.targets:
            raise EndpointReadError("TARGET_NOT_REGISTERED", endpoint_id)
        if endpoint_id not in ENDPOINTS or endpoint_id not in self.enabled_endpoints:
            raise EndpointReadError("CAPABILITY_DISABLED", endpoint_id)
        try:
            response = await asyncio.wait_for(
                self.transport.request(
                    target_id,
                    endpoint_id,
                    method="GET",
                    follow_redirects=False,
                    timeout=self.timeout,
                    max_bytes=MAX_UPSTREAM_BYTES,
                ),
                timeout=self.timeout,
            )
        except TimeoutError:
            raise EndpointReadError("TARGET_TIMEOUT", endpoint_id) from None
        except NormalizationError as exc:
            raise EndpointReadError(exc.code, endpoint_id) from None
        except OSError as exc:
            code = (
                "TARGET_CONNECTION_REFUSED"
                if exc.errno == errno.ECONNREFUSED
                else "TARGET_TIMEOUT"
                if exc.errno == errno.ETIMEDOUT
                else "TARGET_CONNECTION_FAILED"
            )
            raise EndpointReadError(code, endpoint_id) from None
        except (TypeError, AssertionError):
            raise
        except Exception:
            # Never propagate a client repr, certificate, header or URL to logs.
            raise EndpointReadError("TARGET_CONNECTION_FAILED", endpoint_id) from None
        if (
            not isinstance(response, HttpResponse)
            or type(response.status) is not int
            or not 100 <= response.status <= 599
            or not isinstance(response.content_type, str)
            or not isinstance(response.body, bytes)
        ):
            raise EndpointReadError("INVALID_HTTP_RESPONSE", endpoint_id)
        # Reject redirects and authorization before inspecting/decoding any body.
        status = response.status
        if 300 <= status < 400:
            raise EndpointReadError("REDIRECT_BLOCKED", endpoint_id, status)
        if status in (401, 403):
            raise EndpointReadError("ENDPOINT_UNAUTHORIZED", endpoint_id, status)
        if status == 404:
            code = (
                "STARTUP_DATA_UNAVAILABLE"
                if endpoint_id == "startup"
                else "ENDPOINT_NOT_EXPOSED"
            )
            raise EndpointReadError(code, endpoint_id, status)
        if status != 200 and not (status == 503 and endpoint_id in HEALTH_ENDPOINTS):
            raise EndpointReadError("ENDPOINT_UNAVAILABLE", endpoint_id, status)
        media = response.content_type.partition(";")[0].strip().lower()
        if media != "application/json" and media not in {
            "application/vnd.spring-boot.actuator.v2+json",
            "application/vnd.spring-boot.actuator.v3+json",
        }:
            raise EndpointReadError("UNEXPECTED_CONTENT_TYPE", endpoint_id, status)
        try:
            body = loads_bounded(response.body, max_bytes=MAX_UPSTREAM_BYTES)
        except NormalizationError as exc:
            raise EndpointReadError(exc.code, endpoint_id, status) from None
        return ParsedObservation(
            target_id,
            snapshot_id,
            "observation_" + uuid4().hex,
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            endpoint_id,
            body,
            http_status=status,
            representation="actuatorV2" if ".v2+" in media else "actuatorV3",
        )
