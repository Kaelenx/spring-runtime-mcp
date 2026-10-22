"""Offline integration coverage for the actual Bean handler and wire schema."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from spring_runtime_mcp.models.common import ParsedObservation
from spring_runtime_mcp.models.source_catalog import TypeFact
from spring_runtime_mcp.replay import FixtureReader, services_for
from spring_runtime_mcp.tools.inspect_beans import Services, inspect_beans
from spring_runtime_mcp.transport.responses import serialize_response

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(
    (ROOT / "src/spring_runtime_mcp/schemas/tools-contract.json").read_text()
)
SCHEMA = next(
    tool["outputSchema"]
    for tool in CONTRACT["tools"]
    if tool["name"] == "springboot_inspect_beans"
)


def call(arguments: dict[str, Any], services: Services) -> dict[str, Any]:
    result = asyncio.run(inspect_beans(arguments, services))
    Draft202012Validator(SCHEMA).validate(result)
    frame = serialize_response(result, "integration")
    assert len(frame) <= 16_384
    parsed = json.loads(frame)
    assert json.loads(parsed["result"]["content"][0]["text"]) == result
    assert parsed["result"]["structuredContent"] == result
    return result


def test_stripe_interface_factory_property_two_hop_chain() -> None:
    services = services_for("stripe-missing")
    result = call(
        {"typePattern": "example.PaymentGateway", "contextId": "application"},
        services,
    )
    assert result["data"]["items"] == []
    analysis = result["data"]["missingBeanAnalysis"]
    assert analysis["state"] == "explained"
    nodes = {node["id"]: node for node in analysis["conditions"]}
    path = next(path for path in analysis["blockingPaths"] if len(path) >= 3)
    assert nodes[path[0]]["reasonCode"] == "REQUIRED_BEAN_MISSING"
    assert nodes[path[-1]]["reasonCode"] == "PROPERTY_NOT_FOUND"
    assert all(right in nodes[left]["childIds"] for left, right in pairwise(path))
    # The producer's failure is explained, but unrelated observed types have no
    # hierarchy in this catalog, so global interface absence remains uncertain.
    assert result["totalMatched"] is None
    assert result["status"] == "partial"
    assert "SOURCE_TYPE_UNOBSERVED" in result["coverage"]["gaps"]
    assert not json.loads(serialize_response(result))["result"]["isError"]


def test_complete_source_type_evidence_certifies_zero_interface_matches() -> None:
    services = services_for("stripe-missing")
    assert isinstance(services.reader, FixtureReader)
    types = dict(services.catalog.types)
    for context in services.reader.bodies["beans"]["contexts"].values():
        for bean in context["beans"].values():
            types[bean["type"]] = TypeFact(bean["type"], ())
    services.catalog = replace(services.catalog, types=types)
    result = call(
        {"typePattern": "example.PaymentGateway", "contextId": "application"},
        services,
    )
    assert result["totalMatched"] == 0
    assert result["status"] == "ok"
    assert result["data"]["missingBeanAnalysis"]["state"] == "explained"


def test_alias_parent_shadowing_and_both_directions() -> None:
    services = services_for("parent-child")
    result = call(
        {"beanNamePattern": "orders", "contextId": "application", "maxDepth": 3},
        services,
    )
    item = result["data"]["items"][0]
    assert item["name"] == "orderService"
    dependencies = item["dependencies"]
    assert any(
        edge["requestedName"] == "orderRepoAlias"
        and edge["targetName"] == "orderRepository"
        and edge["targetContextId"] == "infrastructure"
        for edge in dependencies
    )
    assert any(
        edge["targetName"] == "client" and edge["targetContextId"] == "application"
        for edge in dependencies
    )
    assert any(edge["targetName"] == "connectionFactory" for edge in dependencies)
    assert item["dependents"]
    repository = call(
        {"beanNamePattern": "orderRepository", "contextId": "infrastructure"},
        services,
    )["data"]["items"][0]
    assert len(repository["dependents"]) >= 2


@pytest.mark.parametrize(
    "arguments",
    [
        {"beanNamePattern": "*", "url": "http://example.invalid"},
        {"beanNamePattern": "*", "maxResults": 51},
        {"beanNamePattern": "\n"},
        {},
        {"beanNamePattern": "*", "targetId": "unregistered"},
    ],
)
def test_invalid_arguments_never_read(arguments: dict[str, Any]) -> None:
    services = services_for("parent-child")
    result = call(arguments, services)
    assert result["data"] is None
    assert isinstance(services.reader, FixtureReader)
    assert services.reader.calls == []


def test_pagination_is_frozen_and_query_bound() -> None:
    services = services_for("parent-child")
    arguments = {"beanNamePattern": "*", "maxResults": 1}
    first = call(arguments, services)
    assert first["nextCursor"]
    assert isinstance(services.reader, FixtureReader)
    initial_calls = list(services.reader.calls)
    second = call({**arguments, "cursor": first["nextCursor"]}, services)
    assert second["data"]["items"][0]["id"] != first["data"]["items"][0]["id"]
    assert second["totalMatched"] == first["totalMatched"]
    assert services.reader.calls == initial_calls
    mismatch = call(
        {**arguments, "maxResults": 2, "cursor": first["nextCursor"]}, services
    )
    assert mismatch["errors"][0]["code"] == "CURSOR_QUERY_MISMATCH"
    assert services.reader.calls == initial_calls


@pytest.mark.parametrize("http_status", [None, 403])
def test_unavailable_conditions_preserve_partial_result(
    http_status: int | None,
) -> None:
    services = services_for("stripe-missing")
    assert isinstance(services.reader, FixtureReader)
    original = services.reader

    class RestrictedReader:
        async def read(
            self, target_id: str, endpoint_id: str, *, snapshot_id: str
        ) -> ParsedObservation:
            observed = await original.read(
                target_id, endpoint_id, snapshot_id=snapshot_id
            )
            if endpoint_id == "conditions":
                return replace(observed, http_status=http_status, body=None)
            return observed

    services.reader = RestrictedReader()
    if http_status is None:
        services.reader = original
        del original.bodies["conditions"]
    result = call({"beanNamePattern": "stripePaymentGateway"}, services)
    assert result["status"] == "partial"
    assert result["data"]["items"] == []
    assert result["data"]["missingBeanAnalysis"]["state"] == "unknown"
    assert result["errors"][0]["endpointId"] == "conditions"


def test_observation_identity_mismatch_is_not_normalized() -> None:
    services = services_for("parent-child")
    original = services.reader

    class WrongIdentityReader:
        async def read(
            self, target_id: str, endpoint_id: str, *, snapshot_id: str
        ) -> ParsedObservation:
            observation = await original.read(
                target_id, endpoint_id, snapshot_id=snapshot_id
            )
            return replace(observation, target_id="other")

    services.reader = WrongIdentityReader()
    result = call({"beanNamePattern": "*"}, services)
    assert result["data"] is None
    assert result["errors"][0]["code"] == "OBSERVATION_IDENTITY_MISMATCH"


def test_many_records_fit_full_frame_and_preserve_total() -> None:
    services = services_for("parent-child")
    assert isinstance(services.reader, FixtureReader)
    beans = {
        "bean" + str(index): {
            "aliases": [],
            "type": "example.Record",
            "scope": "singleton",
            "resource": None,
            "dependencies": [],
        }
        for index in range(300)
    }
    services.reader.bodies["beans"] = {"contexts": {"application": {"beans": beans}}}
    result = call({"beanNamePattern": "*", "maxResults": 50}, services)
    assert result["totalMatched"] == 300
    assert result["truncated"]
    assert result["nextCursor"]
    assert 0 < len(result["data"]["items"]) <= 50


def test_secret_resource_does_not_escape_output_or_cached_page() -> None:
    services = services_for("parent-child")
    assert isinstance(services.reader, FixtureReader)
    secret = "synthetic_resource_credential_487"
    bean = services.reader.bodies["beans"]["contexts"]["application"]["beans"]["client"]
    bean["resource"] = (
        f"jdbc:postgresql://reader:{secret}@db.invalid/data?password={secret}"
    )
    result = call({"beanNamePattern": "client", "maxResults": 1}, services)
    assert secret not in serialize_response(result).decode()
    if result["nextCursor"]:
        page = call(
            {
                "beanNamePattern": "client",
                "maxResults": 1,
                "cursor": result["nextCursor"],
            },
            services,
        )
        assert secret not in serialize_response(page).decode()
