"""Honest end-to-end acceptance for the eight diagnostic scenarios."""

from __future__ import annotations

import asyncio
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.replay import FixtureReader, services_for
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services, inspect_beans
from spring_runtime_mcp.tools.registry import call_tool
from spring_runtime_mcp.transport.responses import serialize_response

ROOT = Path(__file__).resolve().parents[1]


def bean_call(arguments: dict[str, Any]) -> dict[str, Any]:
    """Run the real Bean handler and validate both result representations."""
    result = asyncio.run(inspect_beans(arguments, services_for("stripe-missing")))
    Draft202012Validator(
        definition("springboot_inspect_beans")["outputSchema"]
    ).validate(result)
    frame = json.loads(serialize_response(result, "e2e"))
    assert frame["result"]["structuredContent"] == result
    assert json.loads(frame["result"]["content"][0]["text"]) == result
    assert len(serialize_response(result, "e2e")) < 16_384
    return result


def test_scenario_1_existing_bean_and_two_hop_condition_failure() -> None:
    present = bean_call(
        {"beanNamePattern": "orderService", "contextId": "application", "maxDepth": 3}
    )
    assert present["status"] in ("ok", "partial")
    service = present["data"]["items"][0]
    assert service["name"] == "orderService"
    assert service["type"] == "example.OrderService"
    assert service["dependencies"]
    assert service["dependents"]

    missing = bean_call(
        {"typePattern": "example.PaymentGateway", "contextId": "application"}
    )
    analysis = missing["data"]["missingBeanAnalysis"]
    assert analysis["state"] == "explained"
    nodes = {node["id"]: node for node in analysis["conditions"]}
    path = next(path for path in analysis["blockingPaths"] if len(path) >= 3)
    assert nodes[path[0]]["reasonCode"] == "REQUIRED_BEAN_MISSING"
    assert nodes[path[-1]]["reasonCode"] == "PROPERTY_NOT_FOUND"
    assert all(right in nodes[left]["childIds"] for left, right in pairwise(path))
    assert missing["totalMatched"] is None
    assert missing["status"] == "partial"


def _checked(
    tool: str, arguments: dict[str, Any], services: Services
) -> dict[str, Any]:
    result = asyncio.run(call_tool(tool, arguments, services))
    Draft202012Validator(definition(tool)["outputSchema"]).validate(result)
    frame = json.loads(serialize_response(result, "e2e"))
    assert frame["result"]["structuredContent"] == result
    assert frame["result"]["isError"] is (result["status"] not in ("ok", "partial"))
    return result


def test_scenario_2_servlet_and_webflux_route_mapping() -> None:
    expectations = (
        ("boot-2.7-servlet", "mvc", "Ljavax/servlet/http/HttpServletRequest;"),
        ("boot-3.4-servlet", "mvc", "Ljakarta/servlet/http/HttpServletRequest;"),
        (
            "boot-4.1-webflux",
            "webflux",
            "Lorg/springframework/web/server/ServerWebExchange;",
        ),
    )
    for profile, stack, descriptor in expectations:
        result = _checked(
            "springboot_verify_mappings",
            {"pathPattern": "/orders", "contextId": "application"},
            services_for(profile),
        )
        item = result["data"]["items"][0]
        assert item["stack"] == stack
        assert item["paths"] == ["/orders"] and item["httpMethods"] == ["GET"]
        assert descriptor in item["descriptor"]


def test_scenario_3_health_dependency_down_and_hidden_details() -> None:
    visible = _checked(
        "springboot_diagnose_health",
        {"includeDetails": True},
        services_for("boot-2.7-servlet"),
    )
    paths = [tuple(item["componentPath"]) for item in visible["data"]["items"]]
    assert ("db",) in paths
    assert ("dependencies", "ordersDb") in paths
    assert all(item["status"] == "DOWN" for item in visible["data"]["items"])
    hidden_body = json.loads(
        (ROOT / "tests/fixtures/conformance/faults/health-hidden.json").read_text()
    )
    hidden = _checked(
        "springboot_diagnose_health",
        {},
        Services(
            FixtureReader({"health": hidden_body}),
            SourceCatalog("local", None, {}, (), ()),
            SanitizerPipeline(),
            SnapshotStore(),
        ),
    )
    assert hidden["status"] == "partial"
    assert "HEALTH_DETAILS_NOT_VISIBLE" in hidden["coverage"]["gaps"]
    assert hidden["data"]["items"][0]["remediationCodes"] == [
        "REVIEW_HEALTH_DETAILS_VISIBILITY"
    ]
    assert hidden["data"]["items"][0]["childrenComplete"] is False


def test_scenario_4_flyway_or_liquibase_failure() -> None:
    healthy = _checked(
        "springboot_check_migrations",
        {"engine": "auto"},
        services_for("boot-3.4-servlet"),
    )
    assert healthy["data"]["pendingVisibility"] == "unknown"
    assert any(
        row["migrationBean"] == "ordersFlyway" and row["state"] == "APPLIED"
        for row in healthy["data"]["items"]
    )
    services = services_for("boot-3.4-servlet")
    assert isinstance(services.reader, FixtureReader)
    services.reader.bodies["flyway"]["contexts"]["application"]["flywayBeans"][
        "ordersFlyway"
    ]["migrations"][0]["state"] = "FAILED"
    failed = _checked(
        "springboot_check_migrations",
        {"engine": "flyway", "state": ["FAILED"]},
        services,
    )
    assert failed["data"]["items"]
    assert failed["data"]["items"][0]["state"] == "FAILED"
    assert failed["data"]["items"][0]["attentionRequired"] is True
    assert failed["data"]["items"][0]["migrationBean"] == "ordersFlyway"


def test_scenario_5_startup_top_n_and_parent_chain() -> None:
    result = _checked(
        "springboot_analyze_startup",
        {"minDurationMs": 200},
        services_for("boot-4.1-webflux"),
    )
    by_id = {item["stepId"]: item for item in result["data"]["items"]}
    assert result["data"]["springBootVersion"].startswith("4.1")
    assert by_id["2"]["parentStepId"] == "1"
    assert by_id["3"]["parentStepId"] == "2"
    assert by_id["3"]["ancestorIds"] == ["2", "1"]
    assert (
        result["data"]["items"][0]["durationMs"]
        >= result["data"]["items"][-1]["durationMs"]
    )


def test_scenario_6_openapi_second_order_reference_expansion() -> None:
    result = _checked(
        "springboot_search_openapi",
        {"operationId": "listOrders"},
        services_for("boot-3.4-servlet"),
    )
    schema = result["data"]["items"][0]["responses"][0]["schema"]
    customer = schema["properties"]["customer"]
    assert customer["properties"]["address"]["referenceState"] == "depthLimit"
    assert result["data"]["items"][0]["expansionComplete"] is False


def test_scenario_7_runtime_source_and_profile_drift() -> None:
    from spring_runtime_mcp.models.source_catalog import SourceCatalog, TypeFact
    from spring_runtime_mcp.replay import FixtureReader

    arguments = {
        "expectedBeans": [
            {
                "name": "orderRepoAlias",
                "type": "example.OrderRepository",
                "contextId": "infrastructure",
            },
            {
                "name": "orderService",
                "type": "example.OrderService",
                "contextId": "application",
            },
            {"name": "stripePaymentGateway", "contextId": "application"},
        ],
        "expectedRoutes": [
            {"path": "/payments", "httpMethod": "POST", "contextId": "application"}
        ],
        "expectedMigrationVersion": {
            "engine": "flyway",
            "version": "3",
            "contextId": "application",
            "migrationBean": "ordersFlyway",
        },
        "expectedProfiles": ["prod"],
        "sourceModifiedAt": "2026-10-17T00:00:00Z",
        "maxDepth": 2,
    }
    services = services_for("boot-3.4-servlet")
    result = _checked("springboot_detect_drift", arguments, services)
    assert isinstance(services.reader, FixtureReader)
    assert services.reader.calls == ["beans", "mappings", "flyway", "env", "startup"]
    assert result["status"] == "ok"
    assert result["totalMatched"] == 4
    assert result["data"]["unexpectedInRuntime"] == []
    assert result["source"]["bootVersion"].startswith("3.4")
    assert [
        (item["dimension"], item["identity"], item["expected"], item["actual"])
        for item in result["data"]["missingInRuntime"]
    ] == [
        ("beans", "application:stripePaymentGateway", "present", "absent"),
        ("routes", "application:POST:/payments", "present", "absent"),
        ("migrations", "application:ordersFlyway", "3", "1"),
    ]
    assert result["data"]["missingInRuntime"][0]["affectedDependencyIds"] == []
    assert result["data"]["profileMismatch"] == {
        "expected": ["prod"],
        "actual": ["dev"],
        "state": "mismatch",
    }
    inferences = {item["code"]: item for item in result["data"]["rootCauseInferences"]}
    assert inferences["PROFILE_MISMATCH"]["confidence"] == "supported"
    assert inferences["POSSIBLE_NOT_RESTARTED"]["confidence"] == "candidate"
    assert inferences["INSUFFICIENT_EVIDENCE"]["confidence"] == "unknown"
    assert "ENVIRONMENT_OVERRIDE" not in inferences
    diff_ids = {item["id"] for item in result["data"]["missingInRuntime"]}
    assert inferences["POSSIBLE_NOT_RESTARTED"]["evidenceIds"]
    assert set(inferences["POSSIBLE_NOT_RESTARTED"]["evidenceIds"]) <= diff_ids

    profiles = services_for("boot-3.4-servlet")
    matched = _checked(
        "springboot_detect_drift", {"expectedProfiles": ["dev"]}, profiles
    )
    assert isinstance(profiles.reader, FixtureReader)
    assert profiles.reader.calls == ["env"]
    assert matched["data"]["profileMismatch"]["state"] == "match"
    assert matched["data"]["missingInRuntime"] == []
    assert matched["data"]["rootCauseInferences"] == []
    assert matched["totalMatched"] == 0

    routes = services_for("boot-3.4-servlet")
    extra = _checked(
        "springboot_detect_drift",
        {"expectedRoutes": [], "expectedScopeComplete": True},
        routes,
    )
    assert isinstance(routes.reader, FixtureReader)
    assert routes.reader.calls == ["mappings"]
    assert extra["data"]["unexpectedInRuntime"]
    assert (
        extra["data"]["unexpectedInRuntime"][0]["identity"] == "application:GET:/orders"
    )
    closed = _checked(
        "springboot_detect_drift",
        {"expectedRoutes": [], "expectedScopeComplete": False},
        services_for("boot-3.4-servlet"),
    )
    assert closed["data"]["unexpectedInRuntime"] == []

    ambiguous = _checked(
        "springboot_detect_drift",
        {
            "expectedMigrationVersion": {
                "engine": "flyway",
                "version": "1",
                "contextId": "application",
            }
        },
        services_for("boot-3.4-servlet"),
    )
    assert ambiguous["status"] == "partial"
    assert ambiguous["totalMatched"] is None
    assert "MIGRATION_SCOPE_AMBIGUOUS" in ambiguous["coverage"]["gaps"]
    assert ambiguous["data"]["missingInRuntime"] == []
    unsupported = _checked(
        "springboot_detect_drift",
        {"expectedMigrationVersion": {"engine": "liquibase", "version": "1"}},
        services_for("boot-3.4-servlet"),
    )
    assert "MIGRATION_VERSION_UNSUPPORTED" in unsupported["coverage"]["gaps"]
    assert unsupported["data"]["missingInRuntime"] == []

    typed = services_for("boot-3.4-servlet")
    typed.catalog = SourceCatalog(
        "local",
        None,
        {
            "example.OrderRepository": TypeFact("example.OrderRepository", ()),
            "example.NewOrderRepository": TypeFact("example.NewOrderRepository", ()),
        },
        (),
        (),
    )
    mismatch = _checked(
        "springboot_detect_drift",
        {
            "expectedBeans": [
                {
                    "name": "orderRepository",
                    "type": "example.NewOrderRepository",
                    "contextId": "infrastructure",
                }
            ],
            "maxDepth": 2,
        },
        typed,
    )
    assert mismatch["status"] == "ok"
    item = mismatch["data"]["missingInRuntime"][0]
    assert item["identity"] == "infrastructure:orderRepository"
    assert item["expected"] == "example.NewOrderRepository"
    assert item["actual"] == "example.OrderRepository"
    assert item["evidenceLevel"] == "observed"
    assert set(item["affectedDependencyIds"]) == {"bean_000002", "bean_000003"}
    assert mismatch["data"]["unexpectedInRuntime"] == []

    paged_services = services_for("boot-3.4-servlet")
    paged = _checked(
        "springboot_detect_drift", {**arguments, "maxResults": 1}, paged_services
    )
    assert paged["truncated"] is True and paged["nextCursor"]
    assert paged["totalMatched"] == 4
    assert (
        len(paged["data"]["missingInRuntime"])
        + len(paged["data"]["unexpectedInRuntime"])
        == 1
    )
    assert isinstance(paged_services.reader, FixtureReader)
    seen = list(paged_services.reader.calls)
    resumed = _checked(
        "springboot_detect_drift",
        {**arguments, "maxResults": 1, "cursor": paged["nextCursor"]},
        paged_services,
    )
    assert paged_services.reader.calls == seen
    assert resumed["data"]["missingInRuntime"] or resumed["data"]["unexpectedInRuntime"]
    assert resumed["data"]["profileMismatch"]["state"] == "mismatch"

    rejected_services = services_for("boot-3.4-servlet")
    rejected = _checked(
        "springboot_detect_drift",
        {"expectedProfiles": ["dev"], "sourceModifiedAt": "yesterday"},
        rejected_services,
    )
    assert rejected["status"] == "invalid"
    assert rejected["errors"][0]["code"] == "INVALID_ARGUMENT"
    assert isinstance(rejected_services.reader, FixtureReader)
    assert rejected_services.reader.calls == []


def test_scenario_acceptance_runs_every_diagnostic_handler() -> None:
    from spring_runtime_mcp.tools.registry import HANDLERS

    source = ROOT / "src/spring_runtime_mcp"
    production_python = "\n".join(
        path.read_text(encoding="utf-8") for path in source.rglob("*.py")
    )
    assert "async def inspect_beans" in production_python
    assert "async def diagnose_health" in production_python
    assert "async def verify_mappings" in production_python
    assert "async def check_migrations" in production_python
    assert "async def analyze_startup" in production_python
    assert "async def inspect_config" in production_python
    assert "async def search_openapi" in production_python
    assert "async def detect_drift" in production_python
    assert set(HANDLERS) == {
        "springboot_analyze_startup",
        "springboot_check_migrations",
        "springboot_detect_drift",
        "springboot_diagnose_health",
        "springboot_inspect_beans",
        "springboot_inspect_config",
        "springboot_search_openapi",
        "springboot_verify_mappings",
    }
