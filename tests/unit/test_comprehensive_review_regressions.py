"""CR-01..11: observable budgets and evidence semantics, synthetic inputs only."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from spring_runtime_mcp.adapters.source_catalog import load_source_catalog
from spring_runtime_mcp.contracts.definitions import definition
from spring_runtime_mcp.models.common import AnalysisBudget, ParsedObservation
from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.services import Services
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools import support
from spring_runtime_mcp.tools.registry import call_tool
from spring_runtime_mcp.transport.response_gate import EndpointReadError


class Reader:
    def __init__(self, bodies: dict[str, Any], delay: float = 0) -> None:
        self.bodies = bodies
        self.delay = delay
        self.calls: list[str] = []
        self.active = self.peak = self.cancelled = 0

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        self.calls.append(endpoint_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            body = self.bodies[endpoint_id]
            if isinstance(body, Exception):
                raise body
            return ParsedObservation(
                target_id,
                snapshot_id,
                "review_" + endpoint_id,
                "2026-10-22T00:00:00Z",
                endpoint_id,
                deepcopy(body),
            )
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


def services(reader: Reader) -> Services:
    return Services(
        reader,
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )


def invoke(name: str, args: dict[str, Any], reader: Reader) -> dict[str, Any]:
    result = asyncio.run(call_tool(name, args, services(reader)))
    Draft202012Validator(definition(name)["outputSchema"]).validate(result)
    return result


def document(schemas: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Review", "version": "1"},
        "paths": {},
        "components": {"schemas": schemas},
    }


@pytest.mark.parametrize("keyword", ["allOf", "oneOf", "anyOf", "additionalProperties"])
def test_cr04_composition_keeps_referenced_constraints(keyword: str) -> None:
    ref = {"$ref": "#/components/schemas/Entry"}
    raw = ref if keyword == "additionalProperties" else [ref, {"type": "object"}]
    body = document(
        {
            "Result": {keyword: raw},
            "Entry": {"type": "object", "properties": {"id": {"type": "integer"}}},
        }
    )
    result = invoke(
        "springboot_search_openapi", {"schemaName": "Result"}, Reader({"openapi": body})
    )
    item = result["data"]["items"][0]
    part = (
        item["schema"][keyword]
        if keyword == "additionalProperties"
        else item["schema"][keyword][0]
    )
    assert part["properties"]["id"]["type"] == "integer"
    assert item["expansionComplete"] is True


def test_cr05_path_and_response_refs_resolve_independently_of_schema_depth() -> None:
    body = document({})
    body["paths"] = {"/orders": {"$ref": "#/components/pathItems/Orders"}}
    body["components"].update(
        {
            "pathItems": {
                "Orders": {
                    "get": {
                        "responses": {"200": {"$ref": "#/components/responses/Orders"}}
                    }
                }
            },
            "responses": {
                "Orders": {
                    "description": "Inventory",
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "string"}}
                        }
                    },
                }
            },
        }
    )
    result = invoke(
        "springboot_search_openapi",
        {"pathPattern": "/orders", "maxRefDepth": 0},
        Reader({"openapi": body}),
    )
    assert result["totalMatched"] == 1
    item = result["data"]["items"][0]
    assert item["responses"][0]["schema"]["items"]["type"] == "string"
    assert item["expansionComplete"] is True


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_cr06_ref_siblings_obey_the_document_dialect(version: str) -> None:
    body = document(
        {
            "Result": {"$ref": "#/components/schemas/Name", "maxLength": 3},
            "Name": {"type": "string", "maxLength": 20},
        }
    )
    body["openapi"] = version
    result = invoke(
        "springboot_search_openapi", {"schemaName": "Result"}, Reader({"openapi": body})
    )
    schema = result["data"]["items"][0]["schema"]
    if version.startswith("3.1"):
        assert schema["allOf"][0]["maxLength"] == 20
        assert schema["allOf"][1]["maxLength"] == 3
    else:
        assert schema["maxLength"] == 20 and "allOf" not in schema


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("$id", "https://schemas.example.test/base"),
        ("$dynamicRef", "#node"),
        ("not", {"type": "string"}),
    ],
)
def test_unsupported_schema_semantics_are_never_complete(
    keyword: str, value: Any
) -> None:
    reader = Reader({"openapi": document({"Result": {keyword: value}})})
    result = invoke("springboot_search_openapi", {"schemaName": "Result"}, reader)
    assert result["status"] == "partial" and result["coverage"]["gaps"]
    assert result["data"]["items"][0]["expansionComplete"] is False
    assert reader.calls == ["openapi"]


@pytest.mark.parametrize("level", ["schema", "document"])
@pytest.mark.parametrize("known", [False, True])
def test_cr06_explicit_dialects_do_not_silently_change_semantics(
    level: str, known: bool
) -> None:
    dialect = (
        "https://json-schema.org/draft/2020-12/schema"
        if known
        else "https://schemas.example.test/custom-dialect"
    )
    body = document({"Result": {"type": "string"}})
    if level == "schema":
        body["components"]["schemas"]["Result"]["$schema"] = dialect
    else:
        body["jsonSchemaDialect"] = dialect
    reader = Reader({"openapi": body})
    result = invoke("springboot_search_openapi", {"schemaName": "Result"}, reader)
    assert result["data"]["items"][0]["expansionComplete"] is known
    assert (result["status"] == "ok") is known
    assert reader.calls == ["openapi"]
    if not known:
        assert "OPENAPI_SCHEMA_DIALECT_UNSUPPORTED" in result["coverage"]["gaps"]


def test_cr06_schema_dialect_overrides_the_document_default() -> None:
    body = document(
        {
            "Result": {
                "$schema": "https://spec.openapis.org/oas/3.1/dialect/base",
                "type": "string",
            }
        }
    )
    body["jsonSchemaDialect"] = "https://schemas.example.test/custom-dialect"
    result = invoke(
        "springboot_search_openapi", {"schemaName": "Result"}, Reader({"openapi": body})
    )
    assert result["status"] == "ok"
    assert result["data"]["items"][0]["schema"]["type"] == "string"


@pytest.mark.parametrize(
    "code", ["ENDPOINT_UNAUTHORIZED", "TARGET_TIMEOUT", "CAPABILITY_DISABLED"]
)
def test_cr07_unread_engine_never_becomes_pending(code: str) -> None:
    reader = Reader(
        {
            "flyway": EndpointReadError(code, "flyway"),
            "liquibase": {"contexts": {"application": {"liquibaseBeans": {}}}},
        }
    )
    result = invoke(
        "springboot_check_migrations",
        {
            "expectedMigrations": [
                {
                    "engine": "flyway",
                    "contextId": "application",
                    "migrationBean": "flyway",
                    "identity": "flyway:2",
                    "version": "2",
                }
            ],
            "expectedSetComplete": True,
        },
        reader,
    )
    assert result["status"] == "partial"
    assert result["data"]["items"] == []
    assert result["data"]["pendingVisibility"] == "unknown"
    assert result["errors"][0]["code"] == code


@pytest.mark.parametrize(
    "actual,expected,gap",
    [
        (None, "8:abcd", "CHECKSUM_UNOBSERVED"),
        ("9:abcd", "8:abcd", "CHECKSUM_ALGORITHM_INCOMPATIBLE"),
        ("8:aaaa", "8:bbbb", "CHECKSUM_MISMATCH"),
    ],
)
def test_cr08_checksum_comparison_requires_observed_compatible_values(
    actual: str | None, expected: str, gap: str
) -> None:
    body = {
        "contexts": {
            "application": {
                "liquibaseBeans": {
                    "liquibase": {
                        "changeSets": [
                            {
                                "id": "1",
                                "changeLog": "db.xml",
                                "author": "review",
                                "execType": "EXECUTED",
                                "checksum": actual,
                            }
                        ]
                    }
                }
            }
        }
    }
    result = invoke(
        "springboot_check_migrations",
        {
            "engine": "liquibase",
            "expectedMigrations": [
                {
                    "engine": "liquibase",
                    "contextId": "application",
                    "migrationBean": "liquibase",
                    "identity": "db.xml:1:review",
                    "checksum": expected,
                }
            ],
        },
        Reader({"liquibase": body}),
    )
    assert gap in result["coverage"]["gaps"]
    if gap != "CHECKSUM_MISMATCH":
        assert "CHECKSUM_MISMATCH" not in result["coverage"]["gaps"]


@pytest.mark.parametrize(
    "child_name,aliases", [("gateway", []), ("replacement", ["gateway"])]
)
def test_cr09_type_lookup_honors_local_names_and_aliases(
    child_name: str, aliases: list[str]
) -> None:
    def bean(kind: str, aliases: list[str]) -> dict[str, Any]:
        return {"type": kind, "aliases": aliases, "dependencies": []}

    body = {
        "contexts": {
            "parent": {"beans": {"gateway": bean("example.ParentType", [])}},
            "child": {
                "parentId": "parent",
                "beans": {child_name: bean("example.ChildType", aliases)},
            },
        }
    }
    env = services(Reader({"beans": body}))
    env.catalog = load_source_catalog(
        {
            "targetId": "local",
            "types": [
                {"name": name, "directSupertypes": []}
                for name in ("example.ParentType", "example.ChildType")
            ],
            "producers": [],
        },
        target_id="local",
        budget=AnalysisBudget(),
    )
    result = asyncio.run(
        call_tool(
            "springboot_detect_drift",
            {"expectedBeans": [{"contextId": "child", "type": "example.ParentType"}]},
            env,
        )
    )
    assert result["data"]["missingInRuntime"][0]["actual"] == "absent"
    assert result["status"] == "ok"


@pytest.mark.parametrize(
    "state", ["SUCCESS", "OUT_OF_ORDER", "MISSING_SUCCESS", "FUTURE_SUCCESS"]
)
def test_cr10_applied_migration_states_are_not_absence(state: str) -> None:
    body = {
        "contexts": {
            "application": {
                "flywayBeans": {
                    "flyway": {
                        "migrations": [
                            {"state": state, "version": "3", "installedRank": 3}
                        ]
                    }
                }
            }
        }
    }
    result = invoke(
        "springboot_detect_drift",
        {"expectedMigrationVersion": {"engine": "flyway", "version": "3"}},
        Reader({"flyway": body}),
    )
    assert result["data"]["missingInRuntime"] == []


def test_cr11_hidden_up_preserves_status_but_not_component_absence() -> None:
    result = invoke(
        "springboot_diagnose_health",
        {"componentPath": ["db"]},
        Reader({"health": {"status": "UP"}}),
    )
    assert result["data"]["overallStatus"] == "UP"
    assert result["status"] == "partial" and result["totalMatched"] is None
    assert "HEALTH_DETAILS_NOT_VISIBLE" in result["coverage"]["gaps"]
    visible = invoke(
        "springboot_diagnose_health",
        {"componentPath": ["db"]},
        Reader({"health": {"status": "UP", "components": {}}}),
    )
    assert visible["status"] == "ok" and visible["totalMatched"] == 0


def test_cr02_all_tools_share_two_target_read_slots() -> None:
    reader = Reader({"health": {"status": "UP", "components": {}}}, delay=0.01)
    env = services(reader)

    async def run() -> None:
        results = await asyncio.gather(
            *(call_tool("springboot_diagnose_health", {}, env) for _ in range(8))
        )
        assert all(result["status"] == "ok" for result in results)

    asyncio.run(run())
    assert reader.peak == 2 and len(reader.calls) == 8 and reader.active == 0


def test_cr03_multiread_deadline_cancels_inflight_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(support, "TOOL_TIMEOUT_SECONDS", 0.08)
    reader = Reader(
        {"flyway": {"contexts": {}}, "liquibase": {"contexts": {}}}, delay=0.05
    )
    result = invoke("springboot_check_migrations", {}, reader)
    assert result["errors"][0]["code"] == "TARGET_TIMEOUT"
    assert reader.active == 0 and reader.cancelled == 1


def test_cr02_queued_cancellation_never_opens_an_extra_read() -> None:
    reader = Reader({"health": {"status": "UP", "components": {}}}, delay=0.05)
    env = services(reader)

    async def run() -> None:
        first = [
            asyncio.create_task(call_tool("springboot_diagnose_health", {}, env))
            for _ in range(2)
        ]
        while reader.active != 2:
            await asyncio.sleep(0)
        queued = asyncio.create_task(call_tool("springboot_diagnose_health", {}, env))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await asyncio.gather(*first)
        await call_tool("springboot_diagnose_health", {}, env)

    asyncio.run(run())
    assert reader.peak == 2 and len(reader.calls) == 3 and reader.active == 0
