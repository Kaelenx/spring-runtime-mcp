"""Adversarial checks for boundaries that are easy to overstate."""

import ast
import asyncio
import json
from pathlib import Path

import pytest

from spring_runtime_mcp.models.common import NormalizationError
from spring_runtime_mcp.policy.output_guard import ensure_safe_output
from spring_runtime_mcp.policy.sanitization import MARKER, SanitizerPipeline
from spring_runtime_mcp.tools.inspect_beans import error, failed
from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    HttpResponse,
    ResponseGateReader,
)

from .helpers import HOSTS, SECRET, SpyTransport


@pytest.mark.parametrize("host", HOSTS)
def test_ssrf_spellings_have_no_dynamic_origin_or_dns_path(host, monkeypatch):
    """No socket client exists: only registered logical IDs reach transport."""
    import socket

    resolved = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: resolved.append(a))
    transport = SpyTransport()
    reader = ResponseGateReader(
        transport,
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans", "startup"}),
    )
    with pytest.raises(EndpointReadError):
        asyncio.run(reader.read(host, "beans", snapshot_id="snapshot"))
    assert not resolved
    assert not transport.calls


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_error_body_is_neither_parsed_nor_echoed(status, monkeypatch):
    payload = (
        f"Bearer {SECRET}\npassword={SECRET}\n-----BEGIN PRIVATE KEY-----"
    ).encode()
    transport = SpyTransport(HttpResponse(status, "text/html", payload))
    reader = ResponseGateReader(
        transport, targets=frozenset({"local"}), enabled_endpoints=frozenset({"beans"})
    )

    def parse_forbidden(*args, **kwargs):
        raise AssertionError("error response body must not be parsed")

    monkeypatch.setattr(
        "spring_runtime_mcp.transport.response_gate.loads_bounded", parse_forbidden
    )
    with pytest.raises(EndpointReadError) as caught:
        asyncio.run(reader.read("local", "beans", snapshot_id="snapshot"))
    result = failed(
        caught.value.code,
        endpoint=caught.value.endpoint_id,
        http_status=caught.value.http_status,
    )
    serialized = json.dumps(result)
    assert SECRET not in serialized
    assert "PRIVATE KEY" not in serialized
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", f"Bearer {SECRET}"),
        ("comments", f"jdbc:postgresql://user:{SECRET}@db/app"),
        ("stackTrace", f"password={SECRET}"),
        ("details", {"connection": f"mongodb://user:{SECRET}@db/app"}),
        ("inputs", {"value": f"amqp://user:{SECRET}@broker/vhost"}),
        ("origin", f"redis://user:{SECRET}@cache/0"),
        (
            "value",
            f"-----BEGIN PRIVATE KEY-----\\n{SECRET}\\n-----END PRIVATE KEY-----",
        ),
        ("details", {"jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature"}),
    ],
)
def test_unapproved_runtime_payload_fields_are_dropped_or_redacted(field, value):
    projected = SanitizerPipeline().sanitize({field: value})
    wire_candidate = {"schemaVersion": "2", "data": projected}
    encoded = json.dumps(wire_candidate)
    assert SECRET not in encoded
    assert "PRIVATE KEY" not in encoded
    assert "mongodb://" not in encoded
    assert "jdbc:postgresql://" not in encoded
    assert "redis://" not in encoded
    assert "amqp://" not in encoded
    # A downstream producer cannot bypass the final sink by reintroducing it.
    with pytest.raises(NormalizationError):
        ensure_safe_output({"value": value})


def test_error_metadata_uses_static_endpoint_allowlist_and_never_untrusted_text():
    result = failed(
        "ENDPOINT_UNAUTHORIZED",
        endpoint=f"beans Bearer {SECRET}",
        http_status=401,
    )
    assert result["errors"][0]["endpointId"] is None
    assert SECRET not in json.dumps(result)
    assert result["errors"][0]["message"] != f"beans Bearer {SECRET}"
    assert error("ENDPOINT_UNAUTHORIZED", f"Bearer {SECRET}", 403)["endpointId"] is None


def test_startup_read_is_literal_get_and_redirects_are_disabled():
    transport = SpyTransport()
    reader = ResponseGateReader(
        transport,
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"startup"}),
    )
    asyncio.run(reader.read("local", "startup", snapshot_id="snapshot"))
    assert transport.calls == [
        (
            "local",
            "startup",
            {
                "method": "GET",
                "follow_redirects": False,
                "timeout": 5.0,
                "max_bytes": 8 * 1024 * 1024,
            },
        )
    ]


def test_production_source_has_no_stdout_print_or_default_logger_calls():
    root = Path(__file__).resolve().parents[2] / "src/spring_runtime_mcp"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {
                    "debug",
                    "info",
                    "warning",
                    "error",
                    "critical",
                } or not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"logging", "logger", "log"}
                ), path


def test_untrusted_prose_is_never_an_output_container():
    with pytest.raises(NormalizationError):
        ensure_safe_output({"description": f"Bearer {SECRET}"})
    assert SanitizerPipeline().sanitize({"description": f"Bearer {SECRET}"}) == {
        "description": None
    }
    assert SECRET not in json.dumps(
        SanitizerPipeline().sanitize({"description": f"Bearer {SECRET}"})
    )
    assert (
        SanitizerPipeline().sanitize({"value": f"password={SECRET}"})["value"] == MARKER
    )
