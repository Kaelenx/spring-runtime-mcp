"""Contract regressions for audited kernel defects.

TC-BUG-03 locks the approved graceful-degradation contract: a conditions
failure keeps the Bean result partial and records the code. The other cases
lock the remediated failure behavior.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from spring_runtime_mcp.models.common import NormalizationError, ParsedObservation
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import MARKER, SanitizerPipeline
from spring_runtime_mcp.replay import REPLAY_ROOT, services_for
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services, inspect_beans
from spring_runtime_mcp.transport.response_gate import ResponseGateReader

ROOT = Path(__file__).resolve().parents[2]


def _services(reader: object) -> Services:
    return Services(
        reader,  # type: ignore[arg-type]
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )


def _observation(
    endpoint: str, body: dict[str, object], snapshot_id: str
) -> ParsedObservation:
    return ParsedObservation(
        "local",
        snapshot_id,
        "observation_" + endpoint,
        "2026-10-16T00:00:00Z",
        endpoint,
        body,
    )


def test_tc_bug_01_truncation_reason_must_keep_graph_limit() -> None:
    """GRAPH_LIMIT_REACHED must stay in truncationReasons, not be replaced."""
    beans: dict[str, dict[str, object]] = {}
    for index in range(600):
        name = "onlyTarget" if index == 0 else f"b{index:04d}"
        beans[name] = {
            "type": "example.Filler",
            "scope": "singleton",
            "aliases": [],
            "dependencies": [f"dep{dep:02d}" for dep in range(40)],
        }
    body = {"contexts": {"application": {"parentId": None, "beans": beans}}}

    class Reader:
        async def read(
            self, target_id: str, endpoint_id: str, *, snapshot_id: str
        ) -> ParsedObservation:
            return _observation(endpoint_id, body, snapshot_id)

    result = asyncio.run(
        inspect_beans(
            {"beanNamePattern": "onlyTarget", "maxResults": 1},
            _services(Reader()),
        )
    )
    reasons = result["truncationReasons"]
    gaps = result["coverage"]["gaps"]
    assert result["truncated"] is True
    assert "GRAPH_LIMIT_REACHED" in gaps
    assert "GRAPH_LIMIT_REACHED" in reasons, (
        "truncationReasons dropped the real budget failure and now reads "
        f"{reasons}; coverage.gaps={gaps}. "
        "inspect_beans.py overwrites every truncated result with "
        '["ASSOCIATION_LIMIT"] even when no association was cut.'
    )
    assert reasons != ["ASSOCIATION_LIMIT"]


@pytest.mark.parametrize("scenario", ["stripe-missing", "factory-method"])
def test_tc_bug_02_missing_beans_fixture_must_not_silently_borrow(
    scenario: str,
) -> None:
    """A scenario without beans.json must not quietly reuse parent-child beans."""
    assert not (REPLAY_ROOT / scenario / "beans.json").exists()
    parent = json.loads(
        (REPLAY_ROOT / "parent-child" / "beans.json").read_text(encoding="utf-8")
    )
    try:
        services = services_for(scenario)
    except NormalizationError as exc:
        assert exc.code
        return

    borrowed = services.reader.bodies.get("beans")
    result = asyncio.run(
        inspect_beans(
            {"beanNamePattern": "orderService", "contextId": "application"},
            services,
        )
    )
    public = {
        "status": result["status"],
        "gaps": result["coverage"]["gaps"],
        "warnings": result["warnings"],
        "errors": [item["code"] for item in result["errors"]],
        "endpointIds": result["source"]["endpointIds"],
    }
    explicit = any(
        token in json.dumps(public)
        for token in (
            "FIXTURE_SUBSTITUTED",
            "INPUT_DEGRADED",
            "FIXTURE_MISSING",
            "parent-child",
        )
    )
    names = [
        item["name"] for item in (result["data"]["items"] if result["data"] else [])
    ]
    if borrowed == parent and "orderService" in names and not explicit:
        pytest.fail(
            f"{scenario} has no beans.json, but services_for loaded parent-child "
            f"beans and published {names} as status={result['status']} with no "
            "degradation marker; public=" + json.dumps(public)
        )


def test_tc_bug_03_condition_failure_keeps_partial_evidence() -> None:
    """Beans stay usable when conditions normalization fails.

    The secondary endpoint is recorded, not dropped: status stays partial,
    missing-bean analysis is unknown, and the code is in both errors and gaps.
    """

    class Reader:
        async def read(
            self, target_id: str, endpoint_id: str, *, snapshot_id: str
        ) -> ParsedObservation:
            if endpoint_id == "beans":
                body: dict[str, object] = {
                    "contexts": {
                        "application": {
                            "parentId": None,
                            "beans": {
                                "orderService": {
                                    "type": "example.OrderService",
                                    "scope": "singleton",
                                    "aliases": [],
                                    "dependencies": [],
                                }
                            },
                        }
                    }
                }
            elif endpoint_id == "conditions":
                # Dict-shaped, but not the same context hierarchy as beans.
                body = {"contexts": {}}
            else:
                raise NormalizationError("CAPABILITY_DISABLED")
            return _observation(endpoint_id, body, snapshot_id)

    result = asyncio.run(
        inspect_beans({"beanNamePattern": "definitelyMissing"}, _services(Reader()))
    )
    codes = [item["code"] for item in result["errors"]]
    assert result["status"] == "partial"
    assert result["data"]["missingBeanAnalysis"]["state"] == "unknown"
    assert "CONTEXT_HIERARCHY_CONFLICT" in codes
    assert "CONTEXT_HIERARCHY_CONFLICT" in result["coverage"]["gaps"]


def test_tc_bug_04_invalid_argument_envelope_must_exit_nonzero() -> None:
    """stdout failed(INVALID_ARGUMENT) must not come from a process exit of 0.

    ``--help`` hits ArgumentParser.exit → SystemExit(0). replay.main catches
    that branch and currently keeps exit code 0.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "spring_runtime_mcp.replay", "--help"],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert completed.stderr == b""
    frame = json.loads(completed.stdout)
    envelope = frame["result"]["structuredContent"]
    assert envelope["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert frame["result"]["isError"] is True
    assert completed.returncode != 0, (
        "stdout is failed(INVALID_ARGUMENT) / isError=true, but the process "
        f"exited {completed.returncode}. SystemExit(0) is treated as success "
        f"at replay.py."
    )


def test_tc_bug_05_unknown_diagnostic_code_must_survive() -> None:
    """A structured non-whitelist code must not be relabeled MALFORMED_EVIDENCE."""

    class Reader:
        async def read(
            self, target_id: str, endpoint_id: str, *, snapshot_id: str
        ) -> ParsedObservation:
            raise NormalizationError("UPSTREAM_SCHEMA_CHANGED")

    result = asyncio.run(inspect_beans({"beanNamePattern": "*"}, _services(Reader())))
    assert result["errors"][0]["code"] == "UPSTREAM_SCHEMA_CHANGED", (
        "original code UPSTREAM_SCHEMA_CHANGED was rewritten to "
        f"{result['errors'][0]['code']}; "
        f"nextAction={result['nextAction']['reasonCode']}"
    )
    assert result["nextAction"]["reasonCode"] == "UPSTREAM_SCHEMA_CHANGED"


@pytest.mark.parametrize("exc_type", [TypeError, AssertionError])
def test_tc_bug_06_transport_bug_must_not_become_connection_failure(
    exc_type: type[BaseException],
) -> None:
    """A transport programming error is not TARGET_CONNECTION_FAILED."""

    class BrokenTransport:
        async def request(self, *args: object, **kwargs: object) -> object:
            raise exc_type("synthetic transport defect")

    reader = ResponseGateReader(
        BrokenTransport(),  # type: ignore[arg-type]
        targets=frozenset({"local"}),
        enabled_endpoints=frozenset({"beans"}),
    )
    with pytest.raises(exc_type, match="synthetic transport defect"):
        asyncio.run(reader.read("local", "beans", snapshot_id="tc-bug-06"))


def _urlsafe(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def test_tc_bug_07_corrupt_encoding_must_not_leak_credential() -> None:
    """Decode failures must not skip a recoverable Bearer fragment."""
    header = _urlsafe(b"not-json")
    payload = _urlsafe(b"Bearer zz")
    jose = f"{header}.{payload}.sig"
    broken_base64 = _urlsafe(b"Bearer synthetic-token\xff")
    pipeline = SanitizerPipeline()
    leaks: list[str] = []
    for label, value, needle in (
        ("jose-header-not-json", jose, b"Bearer zz"),
        ("base64-non-utf8", broken_base64, b"Bearer synthetic-token"),
    ):
        published = pipeline.sanitize({"value": value})["value"]
        if published == MARKER:
            continue
        recovered = bytearray()
        for chunk in (published, *published.split(".")):
            if not chunk:
                continue
            padded = chunk + "=" * (-len(chunk) % 4)
            try:
                recovered.extend(base64.urlsafe_b64decode(padded))
            except ValueError:
                continue
        if needle in recovered or needle.decode() in published:
            leaks.append(
                f"{label}: published {published!r}; recovered {bytes(recovered)!r}"
            )
    assert not leaks, (
        "SanitizerPipeline continued past a base64/JOSE decode error and left "
        "a recoverable credential in the sanitized value: " + "; ".join(leaks)
    )
