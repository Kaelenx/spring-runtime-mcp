"""Fixed offline fixture replay using the actual Bean tool handler."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from typing import Any, NoReturn

from .adapters.source_catalog import load_source_catalog
from .models.common import AnalysisBudget, NormalizationError, ParsedObservation
from .models.source_catalog import SourceCatalog
from .policy.sanitization import SanitizerPipeline
from .snapshots import SnapshotStore
from .tools.inspect_beans import Services, failed
from .transport.bounded_json import loads_bounded
from .transport.fixture_files import read_fixture
from .transport.responses import serialize_response
from .transport.stdio import quiet_console, seal_console

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/bean_graph"
CONFORMANCE = FIXTURES.parent / "conformance"
PROFILES = ("boot-2.7-servlet", "boot-3.4-servlet", "boot-4.1-webflux")
_PROFILE_FILES = {
    "beans": "beans.json",
    "conditions": "conditions.json",
    "mappings": "mappings.json",
    "flyway": "flyway.json",
    "liquibase": "liquibase.json",
    "startup": "startup.json",
    "openapi": "openapi.json",
    "env": "env.json",
    "configprops": "configprops.json",
}


class FixtureReader:
    def __init__(self, bodies: dict[str, dict[str, Any]]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        if target_id != "local" or endpoint_id not in self.bodies:
            raise NormalizationError("CAPABILITY_DISABLED")
        self.calls.append(endpoint_id)
        body = self.bodies.get(endpoint_id)
        if body is None:
            raise NormalizationError("CAPABILITY_DISABLED")
        return ParsedObservation(
            target_id,
            snapshot_id,
            "observation_" + endpoint_id,
            "2026-10-16T00:00:00Z",
            endpoint_id,
            body,
        )


def _profile_services(scenario: str) -> Services:
    root = CONFORMANCE / scenario
    bodies: dict[str, dict[str, Any]] = {}
    for endpoint, name in _PROFILE_FILES.items():
        path = root / name
        if path.exists():
            bodies[endpoint] = loads_bounded(read_fixture(CONFORMANCE, path))
    health = root / "health-v3.json"
    if health.exists():
        bodies["health"] = loads_bounded(read_fixture(CONFORMANCE, health))
    return Services(
        FixtureReader(bodies),
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )


def services_for(scenario: str) -> Services:
    if scenario in PROFILES:
        return _profile_services(scenario)
    if scenario not in (
        "parent-child",
        "stripe-missing",
        "factory-method",
        "nested-conditions",
    ):
        raise NormalizationError("INVALID_ARGUMENT")
    beans_path = FIXTURES / scenario / "beans.json"
    substituted = False
    if not beans_path.exists():
        beans_path = FIXTURES / "parent-child/beans.json"
        substituted = True
    bodies = {"beans": loads_bounded(read_fixture(FIXTURES, beans_path))}
    conditions_path = FIXTURES / scenario / "conditions.json"
    if conditions_path.exists():
        bodies["conditions"] = loads_bounded(read_fixture(FIXTURES, conditions_path))
    source_path = FIXTURES / scenario / "source-catalog.json"
    catalog = (
        load_source_catalog(
            loads_bounded(read_fixture(FIXTURES, source_path)),
            target_id="local",
            budget=AnalysisBudget(),
        )
        if source_path.exists()
        else SourceCatalog("local", None, {}, (), ())
    )
    if substituted:
        catalog = SourceCatalog(
            catalog.target_id,
            catalog.source_revision,
            catalog.types,
            catalog.producers,
            (*catalog.gaps, "FIXTURE_SUBSTITUTED"),
        )
    return Services(
        FixtureReader(bodies), catalog, SanitizerPipeline(), SnapshotStore()
    )


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse normally quotes untrusted argv into stderr.
        raise NormalizationError("INVALID_ARGUMENT")


def main() -> None:
    exit_code = 0
    with quiet_console():
        try:
            parser = SafeArgumentParser(
                prog="spring-runtime-mcp-replay", description=__doc__
            )
            parser.add_argument(
                "--scenario",
                choices=(
                    "parent-child",
                    "stripe-missing",
                    "factory-method",
                    "nested-conditions",
                    *PROFILES,
                ),
                required=True,
            )
            parser.add_argument(
                "--tool",
                choices=(
                    "springboot_analyze_startup",
                    "springboot_check_migrations",
                    "springboot_detect_drift",
                    "springboot_diagnose_health",
                    "springboot_inspect_beans",
                    "springboot_inspect_config",
                    "springboot_search_openapi",
                    "springboot_verify_mappings",
                ),
                required=True,
            )
            parser.add_argument("--arguments-json", required=True)
            args = parser.parse_args()
            arguments = loads_bounded(args.arguments_json.encode(), max_bytes=16_384)
            from .tools.registry import call_tool

            result = asyncio.run(
                call_tool(args.tool, arguments, services_for(args.scenario))
            )
            # Offline conformance dependency only; no network/SDK imports.
            from jsonschema import Draft202012Validator

            from .contracts.definitions import definition

            Draft202012Validator(definition(args.tool)["outputSchema"]).validate(result)
        except NormalizationError as exc:
            result = failed(exc.code)
            exit_code = 2 if exc.code == "INVALID_ARGUMENT" else 1
        except (asyncio.CancelledError, KeyboardInterrupt):
            result = failed("REQUEST_CANCELLED")
            exit_code = 130
        except SystemExit as exc:
            result = failed("INVALID_ARGUMENT")
            exit_code = exc.code if isinstance(exc.code, int) and exc.code != 0 else 2
        except Exception:
            result = failed("INTERNAL_ERROR")
            exit_code = 1
        try:
            frame = serialize_response(result) + b"\n"
        except Exception:
            frame = serialize_response(failed("INTERNAL_ERROR")) + b"\n"
            exit_code = 1
    # Libraries never see the real protocol stream during computation.
    try:
        view = memoryview(frame)
        while view:
            view = view[os.write(1, view) :]
    except OSError:
        exit_code = 1
    finally:
        # This command owns the process console until exit, including atexit.
        seal_console()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
