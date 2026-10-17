"""Raw upstream shape matrix plus real Bean/condition normalizer execution."""

import asyncio
import json

import pytest

from spring_runtime_mcp.adapters.source_catalog import load_source_catalog
from spring_runtime_mcp.models.common import AnalysisBudget
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.replay import FixtureReader
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services, inspect_beans

from .assertions import assert_contract
from .conftest import PROFILES


@pytest.mark.parametrize("profile", PROFILES)
def test_mappings_handler_normalizes_stack_and_descriptor(profile):
    from spring_runtime_mcp.replay import services_for
    from spring_runtime_mcp.tools.registry import call_tool

    result = asyncio.run(
        call_tool(
            "springboot_verify_mappings",
            {"pathPattern": "/orders", "contextId": "application"},
            services_for(profile),
        )
    )
    assert_contract("springboot_verify_mappings", result)
    item = result["data"]["items"][0]
    reactive = profile.endswith("webflux")
    assert item["stack"] == ("webflux" if reactive else "mvc")
    assert item["httpMethods"] == ["GET"]
    assert item["paths"] == ["/orders"]
    assert item["controllerClass"] == "example.OrderController"
    assert item["handlerMethod"] == "list"
    if reactive:
        assert "ServerWebExchange" in item["descriptor"]
        assert "reactor/core/publisher/Mono" in item["descriptor"]
    else:
        namespace = "javax" if "2.7" in profile else "jakarta"
        assert f"L{namespace}/servlet/http/HttpServletRequest;" in item["descriptor"]


@pytest.mark.parametrize("profile", PROFILES)
def test_startup_handler_reports_parent_chain_and_duration(profile):
    from spring_runtime_mcp.replay import services_for
    from spring_runtime_mcp.tools.registry import call_tool

    result = asyncio.run(
        call_tool(
            "springboot_analyze_startup",
            {"stepNamePattern": "spring.beans.instantiate"},
            services_for(profile),
        )
    )
    assert_contract("springboot_analyze_startup", result)
    item = result["data"]["items"][0]
    assert result["data"]["springBootVersion"].startswith(profile.split("-")[1])
    assert item["stepId"] == "2" and item["parentStepId"] == "1"
    assert item["ancestorIds"] == ["1"]
    assert item["durationNanos"] == "700000000" and item["durationMs"] == 700
    assert item["bottleneckCode"] == "BEAN_INSTANTIATION"
    assert item["tags"] == [{"key": "beanName", "value": "orderService"}]


@pytest.mark.parametrize("profile", PROFILES)
def test_migration_handler_normalizes_both_engines(profile):
    from spring_runtime_mcp.replay import services_for
    from spring_runtime_mcp.tools.registry import call_tool

    result = asyncio.run(
        call_tool(
            "springboot_check_migrations", {"engine": "auto"}, services_for(profile)
        )
    )
    assert_contract("springboot_check_migrations", result)
    rows = result["data"]["items"]
    flyway = [row for row in rows if row["engine"] == "flyway"]
    assert {row["migrationBean"] for row in flyway} == {"ordersFlyway", "auditFlyway"}
    assert sorted(row["checksum"] for row in flyway) == ["-54321", "12345"]
    assert all(
        row["executionTimeMs"] == 12 and row["state"] == "APPLIED" for row in flyway
    )
    change = next(row for row in rows if row["migrationBean"] == "ordersLiquibase")
    assert change["changeLog"] == "db/changelog/master.yaml"
    assert change["checksum"].startswith("9:") and change["rawState"] == "EXECUTED"
    assert change["version"] is None and change["executionTimeMs"] is None
    assert change["description"] is None and change["state"] == "APPLIED"


def test_health_v2_and_v3_handlers_find_the_down_database(load_sample):
    from spring_runtime_mcp.models.source_catalog import SourceCatalog
    from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
    from spring_runtime_mcp.replay import FixtureReader, services_for
    from spring_runtime_mcp.snapshots import SnapshotStore
    from spring_runtime_mcp.tools.inspect_beans import Services
    from spring_runtime_mcp.tools.registry import call_tool

    modern = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {"includeDetails": True, "componentPath": ["db"]},
            services_for("boot-2.7-servlet"),
        )
    )
    assert_contract("springboot_diagnose_health", modern)
    db = modern["data"]["items"][0]
    assert db["componentPath"] == ["db"] and db["status"] == "DOWN"
    assert db["errorType"] == "java.sql.SQLException"
    assert {"name": "database", "value": "H2"} in db["details"]
    assert "synthetic connection unavailable" not in json.dumps(modern)

    legacy = Services(
        FixtureReader({"health": load_sample("boot-2.7-servlet/health-v2.json")}),
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )
    older = asyncio.run(
        call_tool(
            "springboot_diagnose_health",
            {"includeDetails": True, "componentPath": ["db"]},
            legacy,
        )
    )
    assert_contract("springboot_diagnose_health", older)
    assert older["data"]["items"][0]["componentPath"] == ["db"]
    assert older["data"]["items"][0]["status"] == "DOWN"
    assert {"name": "database", "value": "H2"} in older["data"]["items"][0]["details"]


@pytest.mark.parametrize("profile", PROFILES)
def test_actual_handler_alias_parent_and_two_hop_conditions(profile, load_sample):
    bodies = {
        name: load_sample(f"{profile}/{name}.json") for name in ("beans", "conditions")
    }
    catalog = load_source_catalog(
        load_sample(f"{profile}/source-catalog.json"),
        target_id="local",
        budget=AnalysisBudget(),
    )
    services = Services(
        FixtureReader(bodies), catalog, SanitizerPipeline(), SnapshotStore()
    )
    result = asyncio.run(
        inspect_beans(
            {"typePattern": "example.PaymentGateway", "contextId": "application"},
            services,
        )
    )
    assert_contract("springboot_inspect_beans", result)
    analysis = result["data"]["missingBeanAnalysis"]
    assert analysis["state"] == "explained"
    nodes = {node["id"]: node for node in analysis["conditions"]}
    assert [nodes[i]["reasonCode"] for i in analysis["blockingPaths"][0]] == [
        "REQUIRED_BEAN_MISSING",
        "SOURCE_FACTORY_NOT_CREATED",
        "PROPERTY_NOT_FOUND",
    ]
    assert result["status"] == "ok" and result["totalMatched"] == 0
    nested = bodies["conditions"]["contexts"]["application"]["negativeMatches"][
        "example.PaymentModeCondition"
    ]
    assert "AnyNestedCondition" in nested["notMatched"][0]["message"]
    present = asyncio.run(
        inspect_beans(
            {"beanNamePattern": "orderRepoAlias", "contextId": "infrastructure"},
            services,
        )
    )
    assert_contract("springboot_inspect_beans", present)
    assert present["data"]["items"][0]["name"] == "orderRepository"
    assert any(
        e["targetContextId"] == "infrastructure"
        for e in present["data"]["items"][0]["dependencies"]
    )


@pytest.mark.parametrize("profile", PROFILES)
def test_config_handler_projects_values_and_redacts_secrets(profile):
    from spring_runtime_mcp.replay import FixtureReader, services_for
    from spring_runtime_mcp.tools.registry import call_tool

    services = services_for(profile)
    assert isinstance(services.reader, FixtureReader)
    services.reader.bodies["env"]["propertySources"][0]["properties"][
        "spring.datasource.password"
    ] = {"value": "Bearer synthetic-config-secret", "origin": "systemEnvironment"}
    visible = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"prefix": "spring.application", "includeValues": True},
            services,
        )
    )
    assert_contract("springboot_inspect_config", visible)
    assert visible["data"]["activeProfiles"] == ["dev"]
    item = visible["data"]["items"][0]
    assert item["propertyKey"] == "spring.application.name"
    assert item["effectiveValue"] == "orders" and item["valueState"] == "visible"
    assert item["effective"] == "yes"
    hidden = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"prefix": "spring.datasource", "includeValues": True},
            services,
        )
    )
    assert "synthetic-config-secret" not in json.dumps(hidden)
    assert hidden["data"]["items"][0]["valueState"] == "redacted"
    assert hidden["data"]["items"][0]["effectiveValue"] == "[REDACTED_BY_MCP]"


@pytest.mark.parametrize("profile", PROFILES)
def test_openapi_handler_expands_local_refs_and_stops_at_depth(profile):
    from spring_runtime_mcp.replay import services_for
    from spring_runtime_mcp.tools.registry import call_tool

    result = asyncio.run(
        call_tool(
            "springboot_search_openapi",
            {"pathPattern": "/orders", "httpMethod": "GET"},
            services_for(profile),
        )
    )
    assert_contract("springboot_search_openapi", result)
    item = result["data"]["items"][0]
    schema = item["responses"][0]["schema"]
    assert item["operationId"] == "listOrders"
    assert schema["properties"]["customer"]["properties"]["address"]["type"] == "object"
    assert "city" not in schema["properties"]["customer"]["properties"]["address"]
    assert item["expansionComplete"] is False
    assert "REF_DEPTH_LIMIT" in result["coverage"]["gaps"]
    assert "Success" not in json.dumps(result)


@pytest.mark.parametrize("profile", PROFILES)
def test_real_nested_condition_normalization(profile, load_sample):
    from spring_runtime_mcp.adapters.conditions import normalize_conditions
    from spring_runtime_mcp.models.common import ParsedObservation

    raw = load_sample(f"{profile}/conditions.json")
    observation = ParsedObservation(
        "local", "snapshot", "conditions_obs", "2026-10-16T00:00:00Z", "conditions", raw
    )
    report = normalize_conditions(observation, budget=AnalysisBudget())
    operators = {node.operator for node in report.nodes.values()}
    assert {"any", "all", "none"} <= operators
    assert all(
        child in report.nodes
        for node in report.nodes.values()
        for child in node.child_ids
    )
    assert all(
        node.derived_outcome in ("matched", "notMatched", "unknown")
        for node in report.nodes.values()
    )
    assert any(
        node.child_ids for node in report.nodes.values() if node.operator == "any"
    )


def test_manifest_and_every_json_fixture_is_bounded(load_sample):
    from spring_runtime_mcp.transport.bounded_json import loads_bounded

    from .conftest import ROOT

    manifest = load_sample("manifest.json")
    assert manifest["provenance"] == "synthetic"
    assert manifest["realInstanceIntegrationVerified"] is False
    assert {p["id"] for p in manifest["profiles"]} == set(PROFILES)
    for path in ROOT.rglob("*.json"):
        # Fault matrix is an array; the transport parser intentionally requires
        # an object, so validate this local metadata array through a wrapper.
        raw = path.read_bytes()
        if raw.lstrip().startswith(b"["):
            raw = b'{"cases":' + raw + b"}"
        assert isinstance(loads_bounded(raw), dict)
