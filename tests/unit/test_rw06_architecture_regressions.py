"""Correct-behavior regressions for the remaining architecture-audit items.

These encode the completion standards of RW-06 in
docs/audits/remaining-work-handoff-2026-10-20.md. They are expected to fail
until the minimal fixes land; no production code is replaced and every payload
is synthetic. Failures here are the batch entry point, not a weakened gate.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from spring_runtime_mcp.adapters.migrations import normalize_migrations
from spring_runtime_mcp.adapters.source_catalog import load_source_catalog
from spring_runtime_mcp.models.common import AnalysisBudget, ParsedObservation
from spring_runtime_mcp.models.queries import MigrationQuery
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.configuration import ConfigReadPolicy
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.server import build_services
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services
from spring_runtime_mcp.tools.registry import call_tool


class ObservationReader:
    """Supply synthetic observations while keeping the real handlers intact."""

    def __init__(self, bodies: dict[str, Any]) -> None:
        self.bodies = bodies

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        return ParsedObservation(
            target_id,
            snapshot_id,
            "observation_" + endpoint_id,
            "2026-10-20T00:00:00Z",
            endpoint_id,
            deepcopy(self.bodies[endpoint_id]),
        )


def services_for_bodies(
    bodies: dict[str, Any], *, config_policy: ConfigReadPolicy | None = None
) -> Services:
    return Services(
        ObservationReader(bodies),
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
        config_policy=config_policy,
    )


def _functional_mappings() -> dict[str, Any]:
    return {
        "contexts": {
            "application": {
                "mappings": {
                    "dispatcherHandlers": {
                        "webHandler": [
                            {
                                "handler": "example.RouteConfig$$Lambda$42",
                                "predicate": "{GET [/functional/orders]}",
                            },
                            {
                                "handler": "example.RouteConfig$$Lambda$43",
                                "predicate": "{GET [/functional/items], "
                                "produces [application/json]}",
                            },
                            {
                                "handler": "example.RouteConfig$$Lambda$44",
                                "predicate": "not-a-request-predicate",
                            },
                        ]
                    }
                }
            }
        }
    }


def test_rw06_functional_route_survives_a_path_filter() -> None:
    """RW-06: WebFlux function routes carry method/path evidence in predicates."""
    result = asyncio.run(
        call_tool(
            "springboot_verify_mappings",
            {"pathPattern": "/functional/orders"},
            services_for_bodies({"mappings": _functional_mappings()}),
        )
    )
    assert result["data"] is not None and result["data"]["items"], (
        "RW-06: adapters/mappings.py dropped a functional route because the "
        f"predicate was never parsed: gaps={result['coverage']['gaps']}"
    )
    route = result["data"]["items"][0]
    assert route["paths"] == ["/functional/orders"], route
    assert route["httpMethods"] == ["GET"], route
    assert route["methodsUnrestricted"] is False, route
    assert route["handlerKind"] == "function", route
    assert route["stack"] == "webflux", route
    assert route["matchingComplete"] is True, route

    produces = asyncio.run(
        call_tool(
            "springboot_verify_mappings",
            {"pathPattern": "/functional/items"},
            services_for_bodies({"mappings": _functional_mappings()}),
        )
    )
    assert produces["data"]["items"], (
        "RW-06: predicate conditions must stay parseable: "
        f"gaps={produces['coverage']['gaps']}"
    )
    conditions = produces["data"]["items"][0]["conditions"]
    assert [row for row in conditions if row["kind"] == "produces"], conditions

    assert "MAPPING_PREDICATE_UNPARSED" in result["coverage"]["gaps"], (
        "RW-06: an unparseable predicate must be reported as incomplete evidence: "
        f"gaps={result['coverage']['gaps']}"
    )
    assert result["coverage"]["state"] == "partial", result["coverage"]


def _liquibase_body(change_sets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contexts": {
            "application": {
                "liquibaseBeans": {"ordersLiquibase": {"changeSets": change_sets}}
            }
        }
    }


def test_rw06_liquibase_identity_includes_changelog_and_author() -> None:
    """RW-06: Liquibase identity is changelog/id/author, not bean/id."""
    body = _liquibase_body(
        [
            {
                "id": "001",
                "author": "alice",
                "changeLog": "db/changelog/a.yaml",
                "execType": "EXECUTED",
                "orderExecuted": 1,
            },
            {
                "id": "001",
                "author": "bob",
                "changeLog": "db/changelog/b.yaml",
                "execType": "EXECUTED",
                "orderExecuted": 2,
            },
        ]
    )
    items, _, _ = normalize_migrations(None, body, MigrationQuery())
    assert len(items) == 2, items
    identities = [row["identity"] for row in items]
    assert len(set(identities)) == 2, (
        "RW-06: adapters/migrations.py collided two changeSets that share an id "
        f"but differ in changelog/author: {identities}"
    )
    for row in items:
        assert row["changeLog"] in row["identity"], row
        assert row["author"] in row["identity"], row


def test_rw06_migration_order_puts_attention_first_then_engine_order() -> None:
    """RW-06: abnormal states first, then engine-reported order, never input order."""
    body = _liquibase_body(
        [
            {
                "id": "002",
                "author": "alice",
                "changeLog": "db/changelog/a.yaml",
                "execType": "EXECUTED",
                "orderExecuted": 2,
            },
            {
                "id": "001",
                "author": "alice",
                "changeLog": "db/changelog/a.yaml",
                "execType": "EXECUTED",
                "orderExecuted": 1,
            },
            {
                "id": "003",
                "author": "alice",
                "changeLog": "db/changelog/a.yaml",
                "execType": "FAILED",
                "orderExecuted": 3,
            },
        ]
    )
    items, _, _ = normalize_migrations(None, body, MigrationQuery())
    assert [row["state"] for row in items] == ["FAILED", "APPLIED", "APPLIED"], (
        "RW-06: adapters/migrations.py numbered rows in input order instead of "
        f"attention state then engine order: {[row['state'] for row in items]}"
    )
    assert [row["orderExecuted"] for row in items[1:]] == [1, 2], items
    assert items[0]["attentionRequired"] is True


def _startup_body(count: int) -> dict[str, Any]:
    events = [
        {
            "startupStep": {
                "id": index,
                "name": "spring.beans.instantiate",
                "parentId": None,
            },
            "startTime": "2026-10-20T00:00:00.000Z",
            "endTime": "2026-10-20T00:00:00.010Z",
            "duration": "PT0.01S",
        }
        for index in range(count)
    ]
    return {
        "springBootVersion": "3.4.0",
        "timeline": {"startTime": "2026-10-20T00:00:00.000Z", "events": events},
    }


def test_rw06_startup_node_limit_must_null_total_matched() -> None:
    """RW-06: a capped enumeration cannot publish a concrete population count."""
    result = asyncio.run(
        call_tool(
            "springboot_analyze_startup",
            {},
            services_for_bodies({"startup": _startup_body(501)}),
        )
    )
    assert "STARTUP_NODE_LIMIT" in result["coverage"]["gaps"], result["coverage"]
    assert result["truncated"] is True, result["truncationReasons"]
    assert "STARTUP_NODE_LIMIT" in result["truncationReasons"], (
        "RW-06: analyze_startup.py treated a truncated enumeration as a complete "
        f"count: totalMatched={result['totalMatched']}, roots={result['truncationReasons']}"
    )
    assert result["totalMatched"] is None, (
        "RW-06: totalMatched over a capped timeline must be null, not the visible "
        f"count: {result['totalMatched']}"
    )


def _facts_payload() -> dict[str, Any]:
    return {
        "targetId": "local",
        "sourceRevision": "rev-1",
        "types": [
            {"name": "example.PaymentGateway", "directSupertypes": []},
            {
                "name": "example.StripePaymentGateway",
                "directSupertypes": ["example.PaymentGateway"],
            },
        ],
        "producers": [
            {
                "contextId": "application",
                "beanName": "stripePaymentGateway",
                "aliases": ["paymentGateway"],
                "declaredType": "example.PaymentGateway",
                "implementationType": "example.StripePaymentGateway",
                "configurationClass": "example.PaymentConfiguration",
                "factoryMethod": "stripePaymentGateway",
                "conditionSources": [
                    "example.PaymentConfiguration#stripePaymentGateway"
                ],
                "sourceFile": "src/main/java/example/PaymentConfiguration.java",
                "sourceLine": 12,
            }
        ],
    }


def _payment_beans_body() -> dict[str, Any]:
    return {
        "contexts": {
            "application": {
                "beans": {
                    "stripePaymentGateway": {
                        "aliases": ["paymentGateway"],
                        "scope": "singleton",
                        "type": "example.StripePaymentGateway",
                        "resource": (
                            "class path resource [example/PaymentConfiguration.class]"
                        ),
                        "dependencies": [],
                    }
                }
            }
        }
    }


def test_rw06_online_services_accept_a_registered_fact_catalog() -> None:
    """RW-06: build_services must accept trusted operator-registered facts."""
    catalog = load_source_catalog(
        _facts_payload(), target_id="local", budget=AnalysisBudget()
    )
    services = build_services("http://127.0.0.1:18081", source_catalog=catalog)
    assert services.catalog.producers and services.catalog.types, (
        "RW-06: server.py build_services ignored the registered fact catalog"
    )
    assert "SOURCE_CATALOG_UNPROVIDED" not in services.catalog.gaps


def test_rw06_online_services_mark_missing_facts_as_provenance() -> None:
    """RW-06: online defaults must mark unprovided facts instead of silence."""
    services = build_services("http://127.0.0.1:18081")
    assert services.catalog.producers == () and services.catalog.types == {}
    assert "SOURCE_CATALOG_UNPROVIDED" in services.catalog.gaps, (
        "RW-06: an empty online catalog must record why producers stay unknown: "
        f"gaps={services.catalog.gaps}"
    )


def test_rw06_registered_facts_correlate_an_online_producer() -> None:
    """RW-06: registered facts must reach online producer attribution."""
    catalog = load_source_catalog(
        _facts_payload(), target_id="local", budget=AnalysisBudget()
    )
    services = build_services("http://127.0.0.1:18081", source_catalog=catalog)
    services.reader = ObservationReader({"beans": _payment_beans_body()})
    result = asyncio.run(
        call_tool(
            "springboot_inspect_beans",
            {"beanNamePattern": "stripePaymentGateway"},
            services,
        )
    )
    assert result["data"] is not None and result["data"]["items"], result["errors"]
    item = result["data"]["items"][0]
    assert item["producer"] == {
        "kind": "factoryMethod",
        "configurationClass": "example.PaymentConfiguration",
        "methodName": "stripePaymentGateway",
        "evidenceLevel": "correlated",
    }, item["producer"]

    without = build_services("http://127.0.0.1:18081")
    without.reader = ObservationReader({"beans": _payment_beans_body()})
    plain = asyncio.run(
        call_tool(
            "springboot_inspect_beans",
            {"beanNamePattern": "stripePaymentGateway"},
            without,
        )
    )
    assert plain["data"]["items"][0]["producer"] is None, plain["data"]["items"][0]
    assert "SOURCE_CATALOG_UNPROVIDED" in plain["coverage"]["gaps"], plain["coverage"][
        "gaps"
    ]


def test_rw06_nested_config_props_keep_registered_leaves() -> None:
    """RW-06: nested config objects must not silently disappear."""
    configprops = {
        "contexts": {
            "application": {
                "beans": {
                    "appProps": {
                        "prefix": "app",
                        "properties": {
                            "mode": "dev",
                            "nested": {
                                "inner": "nested-marker-9271",
                                "deeper": {"leaf": "deep-marker-9271"},
                            },
                        },
                    }
                }
            }
        }
    }
    env = {"activeProfiles": [], "propertySources": []}
    result = asyncio.run(
        call_tool(
            "springboot_inspect_config",
            {"prefix": "app"},
            services_for_bodies(
                {"env": env, "configprops": configprops},
                config_policy=ConfigReadPolicy(prefixes=frozenset({"app"})),
            ),
        )
    )
    assert result["data"] is not None, result["errors"]
    keys = [row["propertyKey"] for row in result["data"]["items"]]
    assert "app.nested.inner" in keys, (
        "RW-06: adapters/config.py dropped a nested configprops leaf without a "
        f"projected row or gap: keys={keys}, gaps={result['coverage']['gaps']}"
    )
    assert "app.nested.deeper.leaf" in keys, keys
    nested = [
        row
        for row in result["data"]["items"]
        if row["propertyKey"] == "app.nested.inner"
    ]
    assert nested[0]["bindingState"] == "notReported", nested[0]
    rendered = json.dumps(result)
    assert (
        "nested-marker-9271" not in rendered and "deep-marker-9271" not in rendered
    ), "RW-06: includeValues=false must not leak nested raw values"


def test_root_context_without_parent_id_is_complete() -> None:
    """RW-06: Boot omits parentId for root contexts; that is not unobserved."""
    from spring_runtime_mcp.adapters.beans import normalize_beans

    body = {
        "contexts": {
            "demo": {
                "beans": {
                    "orderController": {
                        "aliases": [],
                        "scope": "singleton",
                        "type": "example.OrderController",
                        "resource": "file:/demo/OrderController.class",
                        "dependencies": [],
                    }
                }
            }
        }
    }
    observation = ParsedObservation(
        "local", "snapshot", "obs", "2026-10-22T00:00:00Z", "beans", body
    )
    graph = normalize_beans(observation, budget=AnalysisBudget())
    context = next(iter(graph.contexts.values()))
    assert context.parent.state == "known" and context.parent.value is None, (
        "a context without parentId is a root context, not unknown parentage"
    )
    assert (
        graph.coverage == "complete" and "PARENT_CONTEXT_UNOBSERVED" not in graph.gaps
    )
