"""Focused diagnostic pairs using one synthetic profile, without an E2E flow."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from spring_runtime_mcp.replay import FixtureReader, services_for
from harness.host import dispatch
from spring_runtime_mcp.tools.inspect_beans import Services


def call(tool: str, arguments: dict[str, Any], services: Services) -> dict[str, Any]:
    frame = asyncio.run(
        dispatch(
            {
                "jsonrpc": "2.0",
                "id": "pair",
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            services,
        )
    )
    assert frame is not None
    result = json.loads(frame)["result"]
    assert result["isError"] is False
    envelope = result["structuredContent"]
    assert envelope["status"] == "ok"
    assert envelope["coverage"]["state"] == "complete"
    assert envelope["errors"] == []
    return envelope


def test_health_distinguishes_healthy_from_unhealthy_dependency() -> None:
    results = {}
    for status in ("UP", "DOWN"):
        services = services_for("boot-3.4-servlet")
        assert isinstance(services.reader, FixtureReader)
        services.reader.bodies["health"] = {
            "status": status,
            "components": {"db": {"status": status, "details": {"database": "H2"}}},
        }
        results[status] = call("springboot_diagnose_health", {}, services)

    healthy, unhealthy = results["UP"], results["DOWN"]
    assert healthy["data"]["overallStatus"] == "UP"
    assert healthy["data"]["items"] == []
    assert healthy["totalMatched"] == 0
    assert unhealthy["data"]["overallStatus"] == "DOWN"
    assert unhealthy["totalMatched"] == 1
    component = unhealthy["data"]["items"][0]
    assert component["componentPath"] == ["db"]
    assert component["status"] == "DOWN"
    assert component["remediationCodes"] == ["REVIEW_DATABASE_CONNECTIVITY"]


def test_mapping_distinguishes_registered_method_from_absent_method() -> None:
    services = services_for("boot-3.4-servlet")
    present = call(
        "springboot_verify_mappings",
        {"pathPattern": "/orders", "httpMethod": "GET"},
        services,
    )
    absent = call(
        "springboot_verify_mappings",
        {"pathPattern": "/orders", "httpMethod": "POST"},
        services,
    )
    assert present["totalMatched"] == 1
    route = present["data"]["items"][0]
    assert route["paths"] == ["/orders"]
    assert route["httpMethods"] == ["GET"]
    assert route["controllerClass"] == "example.OrderController"
    assert absent["totalMatched"] == 0
    assert absent["data"]["items"] == []


def test_startup_distinguishes_slow_step_from_no_step_above_threshold() -> None:
    services = services_for("boot-3.4-servlet")
    arguments = {"stepNamePattern": "spring.beans.instantiate"}
    slow = call(
        "springboot_analyze_startup", {**arguments, "minDurationMs": 500}, services
    )
    no_slow_step = call(
        "springboot_analyze_startup", {**arguments, "minDurationMs": 800}, services
    )
    assert slow["totalMatched"] == 1
    step = slow["data"]["items"][0]
    assert step["name"] == "spring.beans.instantiate"
    assert step["durationMs"] == 700
    assert step["bottleneckCode"] == "BEAN_INSTANTIATION"
    assert no_slow_step["totalMatched"] == 0
    assert no_slow_step["data"]["items"] == []
    assert no_slow_step["data"]["timelineComplete"] is True
    # No matching slow step does not mean startup timing was unobserved.
    assert no_slow_step["data"]["totalDurationMs"] == 900
    assert no_slow_step["data"]["durationBasis"] == "timelineSpan"


def test_config_distinguishes_present_property_from_absent_prefix() -> None:
    services = services_for("boot-3.4-servlet")
    present = call(
        "springboot_inspect_config", {"prefix": "spring.application"}, services
    )
    absent = call(
        "springboot_inspect_config", {"prefix": "missing.application"}, services
    )
    assert present["totalMatched"] == 1
    property_row = present["data"]["items"][0]
    assert property_row["propertyKey"] == "spring.application.name"
    assert property_row["effective"] == "yes"
    assert property_row["configurationBean"] is not None
    assert absent["totalMatched"] == 0
    assert absent["data"]["items"] == []
    assert absent["source"]["endpointIds"] == ["configprops", "env"]


def test_openapi_distinguishes_present_schema_from_absent_schema() -> None:
    services = services_for("boot-3.4-servlet")
    present = call("springboot_search_openapi", {"schemaName": "Address"}, services)
    absent = call(
        "springboot_search_openapi", {"schemaName": "MissingSchema"}, services
    )
    assert present["totalMatched"] == 1
    schema = present["data"]["items"][0]
    assert schema["schemaName"] == "Address"
    assert schema["schema"]["properties"]["city"]["type"] == "string"
    assert schema["expansionComplete"] is True
    assert absent["totalMatched"] == 0
    assert absent["data"]["items"] == []
    assert absent["data"]["openapiVersion"] == "3.0.3"
