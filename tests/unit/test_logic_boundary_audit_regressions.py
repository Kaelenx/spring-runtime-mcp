"""Regression-first contracts for the 2026-10-18 logic/boundary audit.

TC-BUG numbers here belong to docs/audits/logic-and-boundary-audit-2026-10-18.md,
not the older numbered regressions. Production code is never replaced: doubles
are confined to external HTTP/stdin/observation boundaries. All payloads are
synthetic; no test opens an outbound connection. Resource attacks use bounded
observation or cooperative cleanup rather than actually exhausting the process.
"""

from __future__ import annotations

import asyncio
import http.client
import io
import json
import socket
import sys
import threading
from copy import deepcopy
from types import FrameType, SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness.host import dispatch as sdk_dispatch
from spring_runtime_mcp import server
from spring_runtime_mcp.adapters.openapi import _Expander, normalize_openapi
from spring_runtime_mcp.adapters.startup import normalize_startup
from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.models.common import AnalysisBudget, ParsedObservation
from spring_runtime_mcp.models.queries import OpenApiQuery
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.policy.configuration import ConfigReadPolicy
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services
from spring_runtime_mcp.tools.registry import call_tool
from spring_runtime_mcp.transport.actuator_http import ActuatorHttpTransport
from spring_runtime_mcp.transport.response_gate import (
    EndpointReadError,
    HttpResponse,
    ResponseGateReader,
)

ORIGINAL_CREATE_CONNECTION = socket.create_connection


class ObservationReader:
    """Supply immutable synthetic observations, retaining real handler logic."""

    def __init__(self, bodies: dict[str, Any]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        self.calls.append(endpoint_id)
        return ParsedObservation(
            target_id,
            snapshot_id,
            "observation_" + endpoint_id,
            "2026-10-18T00:00:00Z",
            endpoint_id,
            deepcopy(self.bodies[endpoint_id]),
        )


class HttpStub:
    """Replace only the external exchange, not the response gate under test."""

    def __init__(self, response: HttpResponse | Exception) -> None:
        self.response = response

    async def request(self, *args: Any, **kwargs: Any) -> HttpResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def services_for_bodies(
    bodies: dict[str, Any], *, snapshots: SnapshotStore | None = None
) -> Services:
    return Services(
        ObservationReader(bodies),
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        snapshots if snapshots is not None else SnapshotStore(),
        # Config normalization tests exercise explicitly authorized synthetic
        # properties; default host denial is tested independently by TC-BUG-02.
        config_policy=ConfigReadPolicy(
            prefixes=frozenset({"app", "spring"}), allow_values=True, profiles=True
        ),
    )


def services_for_http(
    response: HttpResponse | Exception, *, target: str = "local"
) -> Services:
    targets = frozenset({target})
    return Services(
        ResponseGateReader(
            HttpStub(response), targets, frozenset({"health"}), timeout=0.1
        ),
        SourceCatalog(target, None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
        targets=targets,
    )


def config_env(key: str, value: Any) -> dict[str, Any]:
    return {
        "activeProfiles": [],
        "propertySources": [
            {"name": "systemProperties", "properties": {key: {"value": value}}}
        ],
    }


def test_tc_bug_01_shared_refs_must_obey_global_expansion_budget() -> None:
    """TC-BUG-01: shared refs must stop before 100,000 expansion visits."""
    schemas: dict[str, Any] = {"S5": {"type": "string"}}
    for level in range(5):
        schemas[f"S{level}"] = {
            "type": "object",
            "properties": {
                f"p{index}": {"$ref": f"#/components/schemas/S{level + 1}"}
                for index in range(10)
            },
        }
    document = {
        "openapi": "3.0.3",
        "paths": {},
        "components": {"schemas": schemas},
    }
    visits = 0
    expansion_code = _Expander.expand.__code__

    def observe(frame: FrameType, event: str, arg: Any) -> None:
        nonlocal visits
        if event == "call" and frame.f_code is expansion_code:
            visits += 1
            assert visits <= 100_000, (
                "TC-BUG-01: adapters/openapi.py _Expander exceeded the global "
                "100,000-node safety ceiling before an output gate could run"
            )

    previous_trace = sys.gettrace()
    try:
        # Observe the actual expander without replacing it. Stop safely before
        # the huge 50-way/eight-level payload from the audit can cause OOM.
        sys.settrace(observe)
        _, _, items, gaps, _ = normalize_openapi(
            document, OpenApiQuery(schema_name="S0", max_ref_depth=8)
        )
    finally:
        sys.settrace(previous_trace)
    assert gaps and items and not items[0]["expansionComplete"], (
        "TC-BUG-01: budget exhaustion must be explicit incomplete evidence"
    )


def _omission_codes(tree: Any) -> list[str]:
    """Return the first omission marker found while walking a projected tree."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            codes = node.get("omissionCodes")
            if isinstance(codes, list) and codes:
                return codes
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return []


def test_tc_bug_01_budget_is_per_query_and_leaves_omission_marker() -> None:
    """TC-BUG-01: one shared budget per query; exhaustion leaves explicit omission."""
    schemas: dict[str, Any] = {"S3": {"type": "string"}}
    for level in range(3):
        schemas[f"S{level}"] = {
            "type": "object",
            "properties": {
                f"p{index}": {"$ref": f"#/components/schemas/S{level + 1}"}
                for index in range(10)
            },
        }
    document = {
        "openapi": "3.0.3",
        "paths": {},
        "components": {"schemas": schemas},
    }
    budget = AnalysisBudget(max_visits=64)
    expander = _Expander(document, 8, budget)
    projected, complete = expander.expand(schemas["S0"], 0, frozenset())
    assert complete is False and "REF_EXPANSION_LIMIT" in expander.gaps, (
        "TC-BUG-01: an exhausted expansion budget must be explicit incomplete evidence"
    )
    assert budget.consumed_visits == budget.max_visits + 1, (
        "TC-BUG-01: expansion must stop charging the shared budget once exhausted: "
        f"consumed={budget.consumed_visits}"
    )
    assert _omission_codes(projected) == ["REF_EXPANSION_LIMIT"], (
        "TC-BUG-01: budget stop must keep a bounded omission marker in the projection"
    )

    # A following query must start from a fresh budget, not inherit exhaustion.
    _, _, items, gaps, _ = normalize_openapi(document, OpenApiQuery(schema_name="S3"))
    assert items and items[0]["expansionComplete"] is True and gaps == [], (
        "TC-BUG-01: per-query budgets must not leak into the next query: "
        f"items={items}, gaps={gaps}"
    )


def test_tc_bug_02_config_must_be_disabled_before_any_default_http_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-02: default services must deny config before HTTP is sent."""
    requests: list[tuple[str, str]] = []
    bodies = {
        "/actuator/env": config_env("app.mode", "dev"),
        "/actuator/configprops": {"contexts": {}},
    }

    class Response(io.BytesIO):
        status = 200

        def getheader(self, name: str) -> str:
            return "application/json"

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.path = ""

        def request(self, *, method: str, url: str, headers: Any) -> None:
            requests.append((method, url))
            self.path = url

        def getresponse(self) -> Response:
            return Response(json.dumps(bodies[self.path]).encode())

        def close(self) -> None:
            return None

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    services = server.build_services("http://127.0.0.1:18081")
    result = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"propertyKeyPattern": "*", "includeValues": True},
            services,
        )
    )
    assert requests == [], (
        "TC-BUG-02: server.py build_services enabled config without operator "
        f"authorization; actual HTTP requests={requests}"
    )
    assert result["data"] is None
    assert result["errors"][0]["code"] == "CAPABILITY_DISABLED"


def test_tc_bug_03_registered_host_must_reject_forbidden_dns_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-03: a registered hostname must never connect to metadata IPs."""
    attempts: list[Any] = []

    def resolve(*args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    def refuse_connect(sock: socket.socket, address: Any) -> None:
        # Observe an attempted forbidden connection, but never open it.
        attempts.append(address)
        raise ConnectionRefusedError("synthetic blocked socket")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    # The suite bans create_connection globally. Restore its real implementation
    # for this test only; fake connect below prevents every actual socket dial.
    # Otherwise the suite's ban would masquerade as a production DNS guard.
    monkeypatch.setattr(socket, "create_connection", ORIGINAL_CREATE_CONNECTION)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    transport = ActuatorHttpTransport(
        "https://rebind.example.test", targets=frozenset({"local"})
    )
    try:
        asyncio.run(
            transport.request(
                "local",
                "health",
                method="GET",
                follow_redirects=False,
                timeout=0.1,
                max_bytes=1024,
            )
        )
    except (OSError, ValueError, AssertionError):
        # A policy rejection may occur at registration, resolution or exchange.
        # A socket error after attempting the forbidden IP is never sufficient.
        pass
    assert attempts == [], (
        "TC-BUG-03: actuator_http.py _open handed an unapproved DNS address "
        f"to socket.connect: {attempts}"
    )


def test_tc_bug_04_stdio_must_bound_read_before_allocating_a_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-04: stdin.readline must have a positive bounded size."""

    class InputBoundary:
        def readline(self, size: int = -1) -> bytes:
            assert 0 < size <= server.MAX_FRAME_BYTES + 1, (
                "TC-BUG-04: server.py run_stdio requested an unbounded stdin "
                f"readline(size={size}); the later frame check cannot bound memory"
            )
            return b""

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=InputBoundary()))
    asyncio.run(server.run_stdio(services_for_bodies({})))


def test_tc_bug_04_frame_limit_boundary_ends_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-04: the cap is inclusive and an over-limit frame stops reading."""

    class InputBoundary:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def readline(self, size: int = -1) -> bytes:
            assert size == server.MAX_FRAME_BYTES + 1
            return self.chunks.pop(0) if self.chunks else b""

    base = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
    at_limit = base + b" " * (server.MAX_FRAME_BYTES - len(base) - 1) + b"\n"
    assert len(at_limit) == server.MAX_FRAME_BYTES
    reader = server._BoundedLineReader(io.BytesIO(at_limit))
    assert reader.readline() == at_limit.decode("utf-8"), (
        "TC-BUG-04: a frame exactly at the byte cap must pass the reader"
    )
    assert reader.oversized is False

    over = server._BoundedLineReader(io.BytesIO(b"A" * (server.MAX_FRAME_BYTES + 1)))
    assert over.readline() == "" and over.oversized is True, (
        "TC-BUG-04: an over-cap line must mark the session and stop reading"
    )

    writes = io.StringIO()
    oversized = b"A" * (server.MAX_FRAME_BYTES + 1)
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=InputBoundary([oversized, base + b"\n"])),
    )
    asyncio.run(server.run_stdio(services_for_bodies({}), stdout=writes))
    frames = [json.loads(line) for line in writes.getvalue().splitlines()]
    assert len(frames) == 1, (
        "TC-BUG-04: an oversized frame must end the session instead of re-reading "
        f"its bytes as later requests: frames={frames}"
    )
    assert frames[0]["error"]["code"] == -32600


@pytest.mark.parametrize("include_values", [False, True], ids=["no-values", "values"])
def test_tc_bug_05_sensitive_config_must_preserve_output_schema(
    include_values: bool,
) -> None:
    """TC-BUG-05: redaction must preserve origin types and value states."""
    result = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"prefix": "spring.datasource", "includeValues": include_values},
            services_for_bodies(
                {
                    "env": config_env("spring.datasource.password", "demo"),
                    "configprops": {"contexts": {}},
                }
            ),
        )
    )
    assert result["status"] in {"ok", "partial"} and result["data"]["items"]
    validator = Draft202012Validator(
        definition("springboot_inspect_config")["outputSchema"]
    )

    def leaf_errors(error: Any) -> list[str]:
        if error.context:
            return [
                message for child in error.context for message in leaf_errors(child)
            ]
        return [f"{list(error.absolute_path)}: {error.message}"]

    errors = sorted(
        message
        for error in validator.iter_errors(result)
        for message in leaf_errors(error)
    )
    assert errors == [], (
        "TC-BUG-05: policy/sanitization.py replaced typed config fields "
        "without preserving their contract: " + " | ".join(errors)
    )


def test_tc_bug_06_health_503_must_remain_usable_down_evidence() -> None:
    """TC-BUG-06: valid HTTP 503 health JSON must return DOWN evidence."""
    body = {"status": "DOWN", "components": {"db": {"status": "DOWN"}}}
    result = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {},
            services_for_http(
                HttpResponse(503, "application/json", json.dumps(body).encode())
            ),
        )
    )
    assert result["status"] in {"ok", "partial"} and result["data"] is not None, (
        "TC-BUG-06: response_gate.py discarded valid 503 health JSON: "
        f"status={result['status']}, errors={result['errors']}"
    )
    assert result["data"]["overallStatus"] == "DOWN"
    assert result["data"]["items"][0]["status"] == "DOWN"


def test_tc_bug_07_openapi_must_preserve_parameter_and_request_body() -> None:
    """TC-BUG-07: operation projection must retain actual input schemas."""
    document = {
        "openapi": "3.0.3",
        "paths": {
            "/x": {
                "post": {
                    "parameters": [
                        {"name": "n", "in": "query", "schema": {"type": "integer"}}
                    ],
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    },
                    "responses": {"204": {"description": "ok"}},
                }
            }
        },
    }
    _, _, items, _, _ = normalize_openapi(
        document, OpenApiQuery(path_pattern="/x", http_method="POST")
    )
    item = items[0]
    assert (
        item["parameters"][0]["schema"].get("type") == "integer" and item["requestBody"]
    ), (
        "TC-BUG-07: adapters/openapi.py fabricated operation inputs: "
        f"parameters={item['parameters']}, requestBody={item['requestBody']}, "
        f"expansionComplete={item['expansionComplete']}"
    )
    assert item["requestBody"][0]["schema"]["type"] == "object"


def test_tc_bug_08_config_bindings_must_not_mix_contexts() -> None:
    """TC-BUG-08: each Context/Bean/property binding must remain distinct."""
    configprops = {
        "contexts": {
            "a": {
                "beans": {"cfgA": {"prefix": "app", "properties": {"mode": "alpha"}}}
            },
            "b": {
                "beans": {"cfgB": {"prefix": "app", "properties": {"mode": "bravo"}}}
            },
        }
    }
    result = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"prefix": "app", "includeValues": True},
            services_for_bodies(
                {"env": config_env("app.mode", "dev"), "configprops": configprops}
            ),
        )
    )
    actual = {
        (row["contextId"], row["configurationBean"], row["bindingValue"])
        for row in result["data"]["items"]
        if row["configurationBean"] is not None
    }
    assert actual == {("a", "cfgA", "alpha"), ("b", "cfgB", "bravo")}, (
        f"TC-BUG-08: adapters/config.py merged binding values across Contexts: {actual}"
    )


def test_tc_bug_09_timeout_must_close_the_underlying_http_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-09: request timeout must stop and close its worker exchange."""
    reading = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class Response:
        status = 200

        def read(self, size: int) -> bytes:
            reading.set()
            release.wait(timeout=2)
            return b""

        def getheader(self, name: str) -> str:
            return "application/json"

    class Connection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, **kwargs: Any) -> None:
            return None

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            closed.set()
            release.set()

    monkeypatch.setattr(http.client, "HTTPConnection", Connection)
    reader = ResponseGateReader(
        ActuatorHttpTransport("http://127.0.0.1:18081", targets=frozenset({"local"})),
        frozenset({"local"}),
        frozenset({"health"}),
        timeout=0.1,
    )

    async def exercise() -> None:
        try:
            with pytest.raises(EndpointReadError, match="TARGET_TIMEOUT"):
                await reader.read("local", "health", snapshot_id="timeout_test")
            assert reading.is_set(), (
                "Test precondition: the worker must reach body reading"
            )
            assert closed.is_set(), (
                "TC-BUG-09: response_gate.py timed out but actuator_http.py "
                "left its HTTP worker and connection alive"
            )
        finally:
            # Release the synthetic read even on failure; asyncio.run joins its
            # executor, so this regression leaves no stranded thread behind.
            release.set()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "component", [None, {"status": "FATAL"}], ids=["null", "custom-fatal"]
)
def test_tc_bug_10_health_must_not_hide_invalid_or_custom_components(
    component: Any,
) -> None:
    """TC-BUG-10: invalid/custom component evidence cannot become empty OK."""
    result = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {},
            services_for_bodies(
                {"health": {"status": "UP", "components": {"db": component}}}
            ),
        )
    )
    if component is None:
        assert result["status"] == "partial" and result["coverage"]["gaps"], (
            "TC-BUG-10: health.py filtered a null component without reporting "
            f"missing evidence: status={result['status']}, coverage={result['coverage']}"
        )
    else:
        assert any(row["status"] == "FATAL" for row in result["data"]["items"]), (
            "TC-BUG-10: health.py hid a custom abnormal status as though it "
            f"were healthy: items={result['data']['items']}"
        )


def test_tc_bug_11_startup_span_must_include_separate_root_steps() -> None:
    """TC-BUG-11: timelineSpan must be end minus start, not max(duration)."""
    events = [
        {
            "startTime": f"2026-10-18T00:00:0{start}Z",
            "endTime": f"2026-10-18T00:00:0{start + 1}Z",
            "duration": "PT1S",
            "startupStep": {"id": index, "name": "spring.boot.start", "tags": []},
        }
        for index, start in enumerate((0, 2), start=1)
    ]
    _, total, basis, _, _, _ = normalize_startup(
        {"timeline": {"startTime": "2026-10-18T00:00:00Z", "events": events}},
        step_name_pattern=None,
        bean_name_pattern=None,
        min_duration_ms=0,
        max_depth=4,
    )
    assert (total, basis) == (3000, "timelineSpan"), (
        "TC-BUG-11: adapters/startup.py mislabeled max step duration as "
        f"timeline span: total={total}, basis={basis}"
    )


@pytest.mark.parametrize("name", [[], {}], ids=["array", "object"])
def test_tc_bug_12_non_string_tool_name_must_be_a_controlled_input_error(
    name: Any,
) -> None:
    """TC-BUG-12: unhashable names must not escape as an internal error."""
    frame = asyncio.run(
        sdk_dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            },
            services_for_bodies({}),
            expect_reply=False,
        )
    )
    if frame is None:
        return  # the SDK's typed params reject a non-string name before dispatch
    response = json.loads(frame)
    if "error" in response:
        assert response["error"]["code"] in {-32600, -32602}
    else:
        result = response["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["errors"][0]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize(
    "name", [None, True, "springboot_missing_tool"], ids=["null", "boolean", "unknown"]
)
def test_tc_bug_12_name_type_and_unknown_tool_paths(name: Any) -> None:
    """TC-BUG-12: name errors stay controlled; unknown names are protocol errors."""
    frame = asyncio.run(
        sdk_dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            },
            services_for_bodies({}),
            expect_reply=name == "springboot_missing_tool",
        )
    )
    if frame is None:
        # Null/boolean names do not fit the request shape; the transport drops
        # them without invoking any handler.
        assert not isinstance(name, str)
        return
    body = json.loads(frame)
    if isinstance(name, str):
        # RW-02: MCP and SPEC.md 2.2 classify unknown tool names as protocol
        # errors, not as tool execution results.
        assert "result" not in body and body["error"]["code"] == -32602, body
        return
    if "result" not in body:
        assert body["error"]["code"] in {-32600, -32602}, body
        return
    result = body["result"]
    assert result["isError"] is True
    code = result["structuredContent"]["errors"][0]["code"]
    assert code == "INVALID_ARGUMENT", f"TC-BUG-12: name={name!r} produced {code}"


@pytest.mark.parametrize("failure", [401, "timeout"], ids=["unauthorized", "timeout"])
def test_tc_bug_13_failures_must_keep_the_registered_target_identity(
    failure: Any,
) -> None:
    """TC-BUG-13: failure envelopes for demo must never be attributed to local."""
    response = (
        TimeoutError()
        if failure == "timeout"
        else HttpResponse(401, "application/json", b"{}")
    )
    result = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {"targetId": "demo"},
            services_for_http(response, target="demo"),
        )
    )
    expected_code = (
        "TARGET_TIMEOUT" if failure == "timeout" else "ENDPOINT_UNAUTHORIZED"
    )
    assert result["errors"][0]["code"] == expected_code
    assert result["source"]["targetId"] == "demo", (
        "TC-BUG-13: diagnose_health.py omitted the validated target in failed(): "
        f"source={result['source']}"
    )


def test_tc_bug_14_continuation_must_not_extend_snapshot_absolute_ttl() -> None:
    """TC-BUG-14: pagination at t=59 must not revive t=0 evidence at t=118."""
    now = [0.0]
    services = services_for_bodies(
        {
            "health": {
                "status": "DOWN",
                "components": {f"db{index}": {"status": "DOWN"} for index in range(3)},
            }
        },
        snapshots=SnapshotStore(clock=lambda: now[0]),
    )
    arguments = {"maxResults": 1}
    first = asyncio.run(call_tool("springboot_diagnose_health", arguments, services))
    assert first["nextCursor"] is not None
    now[0] = 59
    second = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {**arguments, "cursor": first["nextCursor"]},
            services,
        )
    )
    assert second["nextCursor"] is not None
    now[0] = 118
    third = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {**arguments, "cursor": second["nextCursor"]},
            services,
        )
    )
    assert third["data"] is None and any(
        error["code"] == "CURSOR_EXPIRED" for error in third["errors"]
    ), (
        "TC-BUG-14: support.py resume/finalize renewed the TTL of an old "
        f"snapshot: status={third['status']}, data={third['data']}"
    )


def test_tc_bug_15_malformed_engine_must_not_discard_other_engine_evidence() -> None:
    """TC-BUG-15: malformed Liquibase must preserve valid Flyway as partial."""
    result = asyncio.run(
        call_tool(
            "springboot_check_migrations",
            {"engine": "auto"},
            services_for_bodies(
                {
                    "flyway": {
                        "contexts": {
                            "application": {
                                "flywayBeans": {
                                    "fw": {
                                        "migrations": [
                                            {"version": "1", "state": "SUCCESS"}
                                        ]
                                    }
                                }
                            }
                        }
                    },
                    "liquibase": {"contexts": None},
                }
            ),
        )
    )
    assert result["status"] == "partial" and result["data"] is not None, (
        "TC-BUG-15: check_migrations.py discarded good Flyway evidence when "
        f"Liquibase normalization failed: status={result['status']}, errors={result['errors']}"
    )
    assert any(
        row["engine"] == "flyway" and row["version"] == "1"
        for row in result["data"]["items"]
    )
    assert any(error["endpointId"] == "liquibase" for error in result["errors"])


def test_tc_bug_02_registration_limits_keys_values_and_profiles() -> None:
    """TC-BUG-02: includeValues and wildcard filters cannot expand registration."""
    env = config_env("app.mode", "dev")
    env["activeProfiles"] = ["private"]
    env["propertySources"][0]["properties"]["app.other"] = {"value": "not-authorized"}
    services = services_for_bodies({"env": env, "configprops": {"contexts": {}}})
    services.config_policy = ConfigReadPolicy(properties=frozenset({"app.mode"}))
    result = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"propertyKeyPattern": "*", "includeValues": True},
            services,
        )
    )
    assert result["status"] == "partial" and result["totalMatched"] is None
    assert "CONFIG_SCOPE_LIMITED" in result["coverage"]["gaps"]
    assert result["data"]["activeProfiles"] is None
    assert [row["propertyKey"] for row in result["data"]["items"]] == ["app.mode"]
    assert result["data"]["items"][0]["effectiveValue"] is None
    assert result["data"]["items"][0]["valueState"] == "notReported"
    assert "not-authorized" not in json.dumps(result)
    Draft202012Validator(
        definition("springboot_inspect_config")["outputSchema"]
    ).validate(result)


def test_tc_bug_02_drift_cannot_bypass_profile_authorization() -> None:
    """TC-BUG-02: enabling a config prefix does not grant Profile reads."""
    services = services_for_bodies({"env": {"activeProfiles": ["private"]}})
    services.config_policy = ConfigReadPolicy(prefixes=frozenset({"app"}))
    result = asyncio.run(
        call_tool("springboot_detect_drift", {"expectedProfiles": ["dev"]}, services)
    )
    assert result["data"] is None
    assert result["errors"][0]["code"] == "CAPABILITY_DISABLED"
    assert services.reader.calls == []


def test_tc_bug_03_mixed_dns_group_is_rejected_before_any_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-03: a good DNS answer cannot hide a forbidden sibling address."""
    attempts = []
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
            for address in ("127.0.0.1", "169.254.169.254")
        ],
    )
    monkeypatch.setattr(
        socket.socket, "connect", lambda sock, address: attempts.append(address)
    )
    transport = ActuatorHttpTransport(
        "https://rebind.example.test", targets=frozenset({"local"})
    )
    with pytest.raises(ValueError, match="ADDRESS_DENIED"):
        asyncio.run(
            transport.request(
                "local",
                "health",
                method="GET",
                follow_redirects=False,
                timeout=0.1,
                max_bytes=1024,
            )
        )
    assert attempts == []


def test_tc_bug_03_peer_mismatch_prevents_all_http_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-BUG-03: even another loopback peer must match the pinned IP exactly."""
    writes = []
    monkeypatch.setattr(socket.socket, "connect", lambda sock, address: None)
    monkeypatch.setattr(socket.socket, "getpeername", lambda sock: ("127.0.0.2", 18081))
    monkeypatch.setattr(
        socket.socket, "sendall", lambda sock, data: writes.append(data)
    )
    transport = ActuatorHttpTransport(
        "http://127.0.0.1:18081", targets=frozenset({"local"})
    )
    with pytest.raises(ValueError, match="PEER_MISMATCH"):
        asyncio.run(
            transport.request(
                "local",
                "health",
                method="GET",
                follow_redirects=False,
                timeout=0.1,
                max_bytes=1024,
            )
        )
    assert writes == []


@pytest.mark.parametrize("group", ["liveness", "readiness"])
def test_tc_bug_06_probes_keep_real_503_status(group: str) -> None:
    """TC-BUG-06: probe groups share the health-only 503 exception."""
    response = HttpResponse(503, "application/json", b'{"status":"DOWN"}')
    services = services_for_http(response)
    services.reader = ResponseGateReader(
        HttpStub(response), frozenset({"local"}), frozenset({"health-" + group})
    )
    result = asyncio.run(
        call_tool("springboot_diagnose_health", {"group": group}, services)
    )
    assert result["status"] == "partial"
    assert result["data"]["overallStatus"] == "DOWN"
    assert result["source"]["endpointIds"] == ["health-" + group]


def test_tc_bug_11_invalid_timestamps_do_not_become_a_known_span() -> None:
    """TC-BUG-11: invalid timestamp evidence must remain unknown."""
    _, total, basis, complete, _, gaps = normalize_startup(
        {
            "timeline": {
                "events": [
                    {
                        "startTime": None,
                        "endTime": "bad",
                        "duration": "PT1S",
                        "startupStep": {"id": 1, "name": "start"},
                    }
                ]
            }
        },
        step_name_pattern=None,
        bean_name_pattern=None,
        min_duration_ms=0,
        max_depth=4,
    )
    assert total is None and basis == "unknown" and complete is False
    assert "STARTUP_TIMING_UNOBSERVED" in gaps


def test_tc_bug_07_local_parameter_and_request_body_references() -> None:
    """TC-BUG-07: referenced input objects retain schemas through the real handler."""
    document = {
        "openapi": "3.0.3",
        "paths": {
            "/x": {
                "parameters": [{"$ref": "#/components/parameters/Count"}],
                "post": {
                    "requestBody": {"$ref": "#/components/requestBodies/Payload"},
                    "responses": {"204": {"description": "ok"}},
                },
            }
        },
        "components": {
            "parameters": {
                "Count": {"name": "n", "in": "query", "schema": {"type": "integer"}}
            },
            "requestBodies": {
                "Payload": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Payload"}
                        }
                    }
                }
            },
            "schemas": {
                "Payload": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            },
        },
    }
    result = asyncio.run(
        call_tool(
            "springboot_search_openapi",
            {"pathPattern": "/x", "httpMethod": "POST"},
            services_for_bodies({"openapi": document}),
        )
    )
    assert result["status"] == "ok"
    item = result["data"]["items"][0]
    assert item["parameters"][0]["schema"]["type"] == "integer"
    assert item["requestBody"][0]["schema"]["properties"]["name"]["type"] == "string"
    assert item["expansionComplete"] is True
    Draft202012Validator(
        definition("springboot_search_openapi")["outputSchema"]
    ).validate(result)


def test_tc_bug_15_invalid_flyway_does_not_become_confirmed_drift() -> None:
    """TC-BUG-15: isolated parse errors must not fabricate confirmed absence in drift."""
    result = asyncio.run(
        call_tool(
            "springboot_detect_drift",
            {"expectedMigrationVersion": {"engine": "flyway", "version": "1"}},
            services_for_bodies({"flyway": {"contexts": None}}),
        )
    )
    assert result["status"] == "partial" and result["totalMatched"] is None
    assert result["data"]["missingInRuntime"] == []
    assert "FLYWAY_EVIDENCE_INVALID" in result["coverage"]["gaps"]
