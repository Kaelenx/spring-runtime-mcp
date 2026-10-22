"""Regression tests for evidence honesty and output-contract gaps."""

from __future__ import annotations

import asyncio
from typing import Any

from spring_runtime_mcp.adapters.migrations import normalize_migrations
from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.models.common import NormalizationError, ParsedObservation
from spring_runtime_mcp.models.queries import MigrationQuery
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.server import _result, _valid_envelope
from spring_runtime_mcp.services import Services
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.registry import call_tool
from conformance.assertions import validator


class Reader:
    def __init__(
        self,
        bodies: dict[str, Any],
        errors: dict[str, NormalizationError] | None = None,
    ) -> None:
        self.bodies = bodies
        self.errors = errors or {}

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        if endpoint_id in self.errors:
            raise self.errors[endpoint_id]
        return ParsedObservation(
            target_id,
            snapshot_id,
            "observation_" + endpoint_id,
            "2026-10-22T00:00:00Z",
            endpoint_id,
            self.bodies[endpoint_id],
        )


def services(reader: Reader) -> Services:
    return Services(
        reader,
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )


def invoke(tool: str, arguments: dict[str, Any], reader: Reader) -> dict[str, Any]:
    return asyncio.run(call_tool(tool, arguments, services(reader)))


def _beans_without_type() -> dict[str, Any]:
    return {
        "contexts": {
            "application": {"beans": {"mystery": {"aliases": [], "dependencies": []}}}
        }
    }


def test_drift_missing_startup_is_a_visible_partial_gap() -> None:
    result = invoke(
        "springboot_detect_drift",
        {
            "expectedBeans": [{"name": "mystery"}],
            "sourceModifiedAt": "2026-10-22T01:00:00Z",
        },
        Reader(
            {"beans": _beans_without_type()},
            {"startup": NormalizationError("ENDPOINT_NOT_EXPOSED")},
        ),
    )
    assert result["status"] == "partial"
    assert result["totalMatched"] is None
    assert "STARTUP_EVIDENCE_UNAVAILABLE" in result["coverage"]["gaps"]
    assert result["errors"] == []


def _mappings(patterns: Any, methods: Any) -> dict[str, Any]:
    return {
        "contexts": {
            "application": {
                "mappings": {
                    "dispatcherServlets": {
                        "dispatcherServlet": [
                            {
                                "handler": "example.C#m",
                                "details": {
                                    "handlerMethod": {
                                        "className": "example.C",
                                        "name": "m",
                                    },
                                    "requestMappingConditions": {
                                        "patterns": patterns,
                                        "methods": methods,
                                    },
                                },
                            }
                        ]
                    }
                }
            }
        }
    }


def test_malformed_mapping_patterns_are_not_a_complete_negative() -> None:
    result = invoke(
        "springboot_verify_mappings",
        {"pathPattern": "/orders"},
        Reader({"mappings": _mappings([123, "x" * 2000], ["GET"])}),
    )
    assert result["status"] == "partial"
    assert result["totalMatched"] is None
    assert "MAPPING_PATTERN_UNREADABLE" in result["coverage"]["gaps"]


def test_unknown_mapping_method_is_not_reported_as_unrestricted() -> None:
    result = invoke(
        "springboot_verify_mappings",
        {"pathPattern": "/orders"},
        Reader({"mappings": _mappings(["/orders"], ["BOGUS"])}),
    )
    assert result["status"] == "partial"
    assert result["totalMatched"] is None
    assert "MAPPING_METHOD_UNREADABLE" in result["coverage"]["gaps"]
    item = result["data"]["items"][0]
    assert item["httpMethods"] == []
    assert item["methodsUnrestricted"] is False
    assert item["matchingComplete"] is False


def _startup(
    start_time: Any, tags: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "springBootVersion": "3.4.0",
        "timeline": {
            "startTime": "2026-10-22T00:00:00Z",
            "events": [
                {
                    "startupStep": {
                        "id": 1,
                        "name": "spring.beans.instantiate",
                        "parentId": None,
                        "tags": tags or [],
                    },
                    "startTime": start_time,
                    "endTime": start_time,
                    "duration": "PT1S",
                }
            ],
        },
    }


def test_invalid_startup_timestamp_is_omitted_and_output_stays_valid() -> None:
    result = invoke(
        "springboot_analyze_startup", {}, Reader({"startup": _startup("not-a-date")})
    )
    assert result["status"] == "partial"
    assert result["data"]["items"][0]["startedAt"] is None
    assert "STARTUP_TIMING_UNOBSERVED" in result["coverage"]["gaps"]
    assert _valid_envelope(
        definition("springboot_analyze_startup")["outputSchema"], result
    )
    validator("springboot_analyze_startup").validate(result)


def test_output_guard_failure_keeps_its_own_error_code() -> None:
    result = _result(
        {"schemaVersion": "2", "data": {"description": "unsafe prose"}},
        None,
    )
    envelope = result.model_dump(by_alias=True)["structuredContent"]
    codes = [item["code"] for item in envelope["errors"]]
    assert codes and codes[0] != "RESPONSE_TOO_LARGE"
    assert codes[0] in {"UNTRUSTED_OUTPUT", "UNSAFE_OUTPUT"}


def test_missing_bean_fields_make_the_result_partial() -> None:
    result = invoke(
        "springboot_inspect_beans",
        {"beanNamePattern": "*"},
        Reader({"beans": _beans_without_type()}),
    )
    assert result["status"] == "partial"
    assert result["totalMatched"] is None
    assert {"TYPE_NOT_REPORTED", "SCOPE_NOT_REPORTED"} <= set(
        result["coverage"]["gaps"]
    )
    assert result["data"]["items"][0]["type"] is None


def test_missing_type_cannot_be_a_concrete_zero_for_a_type_filter() -> None:
    result = invoke(
        "springboot_inspect_beans",
        {"typePattern": "example.PaymentGateway"},
        Reader(
            {
                "beans": _beans_without_type(),
                "conditions": {
                    "contexts": {
                        "application": {
                            "positiveMatches": {},
                            "negativeMatches": {},
                        }
                    }
                },
            }
        ),
    )
    assert result["status"] == "partial"
    assert result["totalMatched"] is None
    assert "TYPE_NOT_REPORTED" in result["coverage"]["gaps"]


def test_health_detail_truncation_is_explicit() -> None:
    details = {f"k{index}": f"v{index}" for index in range(15)}
    result = invoke(
        "springboot_diagnose_health",
        {"includeDetails": True, "includeHealthy": True},
        Reader(
            {
                "health": {
                    "status": "UP",
                    "components": {"db": {"status": "UP", "details": details}},
                }
            }
        ),
    )
    assert result["status"] == "partial"
    assert result["truncated"] is True
    assert result["totalMatched"] is None
    assert "HEALTH_DETAILS_LIMIT" in result["coverage"]["gaps"]
    assert "HEALTH_DETAILS_LIMIT" in result["truncationReasons"]
    assert len(result["data"]["items"][0]["details"]) == 10


def test_startup_tag_truncation_is_explicit() -> None:
    tags = [{"key": f"k{index}", "value": "v"} for index in range(15)]
    result = invoke(
        "springboot_analyze_startup",
        {},
        Reader({"startup": _startup("2026-10-22T00:00:00Z", tags)}),
    )
    assert result["status"] == "partial"
    assert result["truncated"] is True
    assert result["totalMatched"] is None
    assert "STARTUP_TAG_LIMIT" in result["coverage"]["gaps"]
    assert "STARTUP_TAG_LIMIT" in result["truncationReasons"]


def test_local_coverage_scope_is_not_redacted() -> None:
    result = invoke(
        "springboot_diagnose_health",
        {},
        Reader(
            {
                "health": {
                    "status": "UP",
                    "components": {},
                }
            }
        ),
    )
    assert result["coverage"]["scope"] == "registered target health"
    assert "/coverage/scope" not in result["redactions"]


def test_migration_budget_exhaustion_is_not_malformed_upstream_data() -> None:
    body = {
        "contexts": {
            "application": {
                "flywayBeans": {
                    "flyway": {"migrations": [{"version": "x"} for _ in range(20_001)]}
                }
            }
        }
    }
    rows, gaps, _visibility = normalize_migrations(
        body, None, MigrationQuery(engine="flyway")
    )
    assert rows == []
    assert "GRAPH_LIMIT_REACHED" in gaps
    assert "FLYWAY_EVIDENCE_INVALID" not in gaps


def test_packaged_replay_data_is_not_read_from_tests() -> None:
    from spring_runtime_mcp.replay import REPLAY_ROOT, services_for

    assert REPLAY_ROOT.is_dir()
    assert "tests" not in REPLAY_ROOT.parts
    replay_services = services_for("parent-child")
    assert replay_services.reader is not None
