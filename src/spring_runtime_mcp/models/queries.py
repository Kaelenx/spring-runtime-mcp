"""Validated immutable tool arguments."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InspectBeansQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    bean_name_pattern: str | None = None
    type_pattern: str | None = None
    package_prefix: str | None = None
    context_id: str | None = None
    direction: Literal["dependencies", "dependents", "both"] = "both"
    max_depth: int = 2
    condition_depth: int = 4


@dataclass(frozen=True)
class HealthQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    component_path: tuple[str, ...] = ()
    group: Literal["application", "liveness", "readiness"] = "application"
    include_details: bool = False
    include_healthy: bool = False
    max_depth: int = 4


@dataclass(frozen=True)
class MappingQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    path_pattern: str | None = None
    http_method: str | None = None
    controller_class_pattern: str | None = None
    context_id: str | None = None


@dataclass(frozen=True)
class ExpectedMigration:
    engine: str
    context_id: str
    migration_bean: str
    identity: str
    version: str | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class StartupQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    step_name_pattern: str | None = None
    bean_name_pattern: str | None = None
    min_duration_ms: float = 0
    max_depth: int = 4


@dataclass(frozen=True)
class ConfigQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    prefix: str | None = None
    property_key_pattern: str | None = None
    context_id: str | None = None
    include_values: bool = False


@dataclass(frozen=True)
class OpenApiQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    path_pattern: str | None = None
    http_method: str | None = None
    operation_id: str | None = None
    schema_name: str | None = None
    max_ref_depth: int = 2


@dataclass(frozen=True)
class MigrationQuery:
    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    engine: Literal["auto", "flyway", "liquibase"] = "auto"
    context_id: str | None = None
    migration_bean: str | None = None
    states: tuple[str, ...] = ()
    version: str | None = None
    expected: tuple[ExpectedMigration, ...] = ()
    expected_set_complete: bool = False


@dataclass(frozen=True)
class ExpectedBean:
    name: str | None = None
    type_name: str | None = None
    context_id: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExpectedRoute:
    path: str
    http_method: str
    controller_class: str | None = None
    context_id: str | None = None


@dataclass(frozen=True)
class ExpectedMigrationVersion:
    engine: Literal["flyway", "liquibase"]
    version: str
    context_id: str | None = None
    migration_bean: str | None = None


@dataclass(frozen=True)
class DriftQuery:
    """Omitted expectation fields stay None; empty tuples are explicit scopes."""

    target_id: str = "local"
    max_results: int = 50
    cursor: str | None = None
    expected_beans: tuple[ExpectedBean, ...] | None = None
    expected_routes: tuple[ExpectedRoute, ...] | None = None
    expected_migration: ExpectedMigrationVersion | None = None
    expected_profiles: tuple[str, ...] | None = None
    expected_scope_complete: bool = False
    source_revision: str | None = None
    source_modified_at: str | None = None
    max_depth: int = 2
