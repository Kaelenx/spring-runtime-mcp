"""Compare caller expectations with one runtime snapshot.

Omitted dimensions are not read and not compared. An empty expectation is an
explicit empty scope. Unexpected runtime objects are reported only when the
caller marks that scope complete and the corresponding observation is complete.
"""

from __future__ import annotations

import re
from typing import Any

from ..adapters.beans import normalize_beans
from ..adapters.mappings import normalize_mappings
from ..adapters.migrations import normalize_migrations
from ..analyzers.bean_graph import (
    build_dependency_graph,
    resolve_bean_reference,
    slice_dependencies,
    visible_beans,
)
from ..analyzers.condition_failures import is_assignable
from ..models.bean_graph import Bean, BeanGraph, BeanKey, ContextKey
from ..models.common import AnalysisBudget, AnalysisLimit, ParsedObservation
from ..models.queries import (
    DriftQuery,
    ExpectedBean,
    ExpectedRoute,
    MigrationQuery,
)
from ..models.source_catalog import SourceCatalog

_IDENTITY = re.compile(r"[A-Za-z0-9_$][A-Za-z0-9_.$#/@:+-]*\Z")
_FACT = re.compile(r"[A-Za-z0-9_.$#/@:+-]{1,256}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
_BRACE = re.compile(r"\{([A-Za-z0-9_$]+)\}")
_DIM = {"beans": 0, "routes": 1, "migrations": 2}
_FLYWAY_GAPS = frozenset(
    {
        "FLYWAY_EVIDENCE_INVALID",
        "FLYWAY_BEANS_UNOBSERVED",
        "FLYWAY_MIGRATIONS_UNOBSERVED",
        "MIGRATION_RECORD_UNREADABLE",
    }
)


def _public_identity(value: str) -> str | None:
    folded = _BRACE.sub(r":\1", value)
    if len(folded) > 256 or _IDENTITY.fullmatch(folded) is None:
        return None
    return folded


def _fact(value: str) -> str | None:
    if _FACT.fullmatch(value) is None:
        return None
    return value


def _record(
    dimension: str,
    identity: str,
    context_id: str | None,
    expected: str,
    actual: str,
    evidence: str,
    affected: list[str],
) -> dict[str, Any] | None:
    public = _public_identity(identity)
    safe_expected = _fact(expected)
    safe_actual = _fact(actual)
    if public is None or safe_expected is None or safe_actual is None:
        return None
    if evidence not in ("observed", "inferred", "unknown"):
        return None
    return {
        "dimension": dimension,
        "identity": public,
        "contextId": context_id,
        "expected": safe_expected,
        "actual": safe_actual,
        "evidenceLevel": evidence,
        "affectedDependencyIds": affected[:16],
    }


def _sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            _DIM[row["dimension"]],
            row["contextId"] or "",
            row["identity"],
        ),
    )


def _affected(
    graph: BeanGraph, key: BeanKey, max_depth: int, budget: AnalysisBudget
) -> tuple[list[str], bool]:
    sliced = slice_dependencies(
        graph, (key,), direction="dependents", max_depth=max_depth, budget=budget
    )
    found: list[str] = []
    for edge, _depth in sliced.edges:
        bean = graph.beans.get(edge.from_key)
        if bean is None or bean.id in found:
            continue
        found.append(bean.id)
    found.sort()
    return found[:16], sliced.truncated or len(found) > 16


def _type_relation(
    bean: Bean, expected: str, catalog: SourceCatalog, budget: AnalysisBudget
) -> str:
    reported = bean.reported_type
    if reported.state != "known" or not isinstance(reported.value, str):
        return "unreported"
    if reported.value == expected:
        return "match"
    try:
        fact = is_assignable(reported.value, expected, catalog, budget=budget)
    except AnalysisLimit:
        return "unknown"
    if fact.state != "known" or not isinstance(fact.value, bool):
        return "unknown"
    return "match" if fact.value else "mismatch"


def _aliases_match(bean: Bean, aliases: tuple[str, ...]) -> bool:
    present = set(bean.aliases)
    return all(alias in present for alias in aliases)


def _bean_records(
    query: DriftQuery,
    observation: ParsedObservation,
    catalog: SourceCatalog,
    gaps: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    budget = AnalysisBudget()
    graph = build_dependency_graph(
        normalize_beans(observation, budget=budget), budget=budget
    )
    incomplete = graph.coverage != "complete"
    if incomplete:
        gaps.extend(graph.gaps)
    expectations = query.expected_beans or ()
    missing: list[dict[str, Any]] = []
    covered: set[BeanKey] = set()
    blocked = False
    for expected in expectations:
        outcome, keys, blocked_one = _one_bean(
            graph, observation, expected, catalog, budget, gaps, query.max_depth
        )
        blocked = blocked or blocked_one or expected.context_id is None
        covered.update(keys)
        if outcome is not None:
            missing.append(outcome)
    unexpected: list[dict[str, Any]] = []
    scope_closed = (
        query.expected_scope_complete
        and not blocked
        and graph.coverage == "complete"
        and expectations is not None
    )
    if scope_closed:
        for key, bean in sorted(graph.beans.items()):
            if key in covered:
                continue
            identity = f"{key.context.context_id}:{key.canonical_name}"
            row = _record(
                "beans",
                identity,
                key.context.context_id,
                "absent",
                "present",
                "observed",
                [],
            )
            if row is None:
                gaps.append("IDENTITY_NOT_SAFE")
                incomplete = True
                continue
            unexpected.append(row)
    return missing, unexpected, incomplete


def _one_bean(
    graph: BeanGraph,
    observation: ParsedObservation,
    expected: ExpectedBean,
    catalog: SourceCatalog,
    budget: AnalysisBudget,
    gaps: list[str],
    max_depth: int,
) -> tuple[dict[str, Any] | None, set[BeanKey], bool]:
    """Return a diff, the runtime keys that satisfy the expectation, and whether
    unexpected enumeration must be suppressed.
    """
    keys = _candidate_beans(graph, observation, expected)
    if keys is None:
        gaps.append("BEAN_INDEX_INCOMPLETE")
        return None, set(), True
    if len(keys) > 1 and expected.name is not None:
        satisfying = [
            key
            for key in keys
            if _bean_satisfies(graph, key, expected, catalog, budget, gaps) == "match"
        ]
        if satisfying:
            return None, set(satisfying), False
        gaps.append("BEAN_SCOPE_AMBIGUOUS")
        return None, set(), True
    if expected.name is None:
        return _type_only(
            graph, observation, expected, keys, catalog, budget, gaps, max_depth
        )
    if not keys:
        row = _absent_bean(graph, observation, expected, gaps)
        return row, set(), row is None
    key = keys[0]
    relation = _bean_satisfies(graph, key, expected, catalog, budget, gaps)
    if relation == "match":
        return None, {key}, False
    if relation != "mismatch":
        return None, set(), True
    row, cut = _mismatch_record(graph, key, expected, catalog, budget, gaps, max_depth)
    return row, {key}, cut


def _bean_satisfies(
    graph: BeanGraph,
    key: BeanKey,
    expected: ExpectedBean,
    catalog: SourceCatalog,
    budget: AnalysisBudget,
    gaps: list[str],
) -> str:
    bean = graph.beans[key]
    if expected.type_name:
        relation = _type_relation(bean, expected.type_name, catalog, budget)
        if relation == "unreported":
            gaps.append("TYPE_NOT_REPORTED")
            return "unknown"
        if relation == "unknown":
            gaps.append("SOURCE_TYPE_UNOBSERVED")
            return "unknown"
        if relation == "mismatch":
            return "mismatch"
    if not _aliases_match(bean, expected.aliases):
        return "mismatch"
    return "match"


def _candidate_beans(
    graph: BeanGraph, observation: ParsedObservation, expected: ExpectedBean
) -> list[BeanKey] | None:
    """None means the searched hierarchy is not fully visible."""
    if expected.name is None:
        return _beans_by_type(graph, observation, expected)
    starts = _start_contexts(graph, observation, expected.context_id)
    if starts is None:
        return None
    found: list[BeanKey] = []
    for start in starts:
        resolution = resolve_bean_reference(graph, start, expected.name)
        if resolution.state == "ambiguous":
            return None
        if resolution.state == "resolved":
            found.append(resolution.candidates[0])
            continue
        if resolution.reason_code != "NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY":
            return None
    unique = sorted(set(found))
    return unique


def _beans_by_type(
    graph: BeanGraph, observation: ParsedObservation, expected: ExpectedBean
) -> list[BeanKey] | None:
    if expected.type_name is None:
        return []
    if expected.context_id is None:
        if graph.coverage != "complete":
            return None
        return sorted(graph.beans)
    start = ContextKey(
        observation.target_id, observation.snapshot_id, expected.context_id
    )
    if start not in graph.contexts:
        return [] if graph.coverage == "complete" else None
    visible = visible_beans(graph, start)
    return None if visible is None else sorted(visible)


def _start_contexts(
    graph: BeanGraph, observation: ParsedObservation, context_id: str | None
) -> list[ContextKey] | None:
    if context_id is None:
        if graph.coverage != "complete":
            return None
        return sorted(graph.contexts)
    key = ContextKey(observation.target_id, observation.snapshot_id, context_id)
    if key not in graph.contexts:
        return [] if graph.coverage == "complete" else None
    return [key]


def _absent_bean(
    graph: BeanGraph,
    observation: ParsedObservation,
    expected: ExpectedBean,
    gaps: list[str],
) -> dict[str, Any] | None:
    if not _absence_is_observed(graph, observation, expected):
        gaps.append("BEAN_INDEX_INCOMPLETE")
        return None
    identity = (
        f"{expected.context_id}:{expected.name}"
        if expected.context_id and expected.name
        else f"unscoped:{expected.name or expected.type_name or 'bean'}"
    )
    row = _record(
        "beans",
        identity,
        expected.context_id,
        expected.type_name or "present",
        "absent",
        "observed",
        [],
    )
    if row is None:
        gaps.append("IDENTITY_NOT_SAFE")
    return row


def _type_only(
    graph: BeanGraph,
    observation: ParsedObservation,
    expected: ExpectedBean,
    keys: list[BeanKey],
    catalog: SourceCatalog,
    budget: AnalysisBudget,
    gaps: list[str],
    max_depth: int,
) -> tuple[dict[str, Any] | None, set[BeanKey], bool]:
    if expected.type_name is None:
        gaps.append("BEAN_INDEX_INCOMPLETE")
        return None, set(), True
    matched: set[BeanKey] = set()
    mismatches: list[BeanKey] = []
    unknown = False
    for key in keys:
        relation = _type_relation(graph.beans[key], expected.type_name, catalog, budget)
        if relation == "match" and _aliases_match(graph.beans[key], expected.aliases):
            matched.add(key)
        elif relation == "match":
            mismatches.append(key)
        elif relation in ("unknown", "unreported"):
            unknown = True
    if matched:
        return None, matched, False
    if len(mismatches) == 1 and not unknown:
        row, cut = _mismatch_record(
            graph, mismatches[0], expected, catalog, budget, gaps, max_depth
        )
        return row, {mismatches[0]}, cut or row is None
    if mismatches or unknown:
        gaps.append("BEAN_SCOPE_AMBIGUOUS" if mismatches else "SOURCE_TYPE_UNOBSERVED")
        return None, set(), True
    if not _absence_is_observed(graph, observation, expected):
        gaps.append("BEAN_INDEX_INCOMPLETE")
        return None, set(), True
    row = _absent_bean(graph, observation, expected, gaps)
    return row, set(), row is None


def _mismatch_record(
    graph: BeanGraph,
    key: BeanKey,
    expected: ExpectedBean,
    catalog: SourceCatalog,
    budget: AnalysisBudget,
    gaps: list[str],
    max_depth: int,
) -> tuple[dict[str, Any] | None, bool]:
    bean = graph.beans[key]
    affected, cut = _affected(graph, key, max_depth, budget)
    if cut:
        gaps.append("AFFECTED_DEPENDENCY_LIMIT")
    if (
        expected.type_name
        and _type_relation(bean, expected.type_name, catalog, budget) == "mismatch"
    ):
        expected_text = expected.type_name
        actual_text = bean.reported_type.value or "unknown"
    else:
        expected_text = next(
            (alias for alias in expected.aliases if alias not in bean.aliases), "alias"
        )
        actual_text = "alias-absent"
    row = _record(
        "beans",
        f"{key.context.context_id}:{key.canonical_name}",
        key.context.context_id,
        expected_text,
        actual_text,
        "observed",
        affected,
    )
    if row is None:
        gaps.append("IDENTITY_NOT_SAFE")
        return None, True
    return row, cut


def _absence_is_observed(
    graph: BeanGraph, observation: ParsedObservation, expected: ExpectedBean
) -> bool:
    if expected.context_id is None:
        return graph.coverage == "complete"
    key = ContextKey(
        observation.target_id, observation.snapshot_id, expected.context_id
    )
    if key not in graph.contexts:
        return graph.coverage == "complete"
    if expected.name is None:
        return True
    resolution = resolve_bean_reference(graph, key, expected.name)
    return resolution.reason_code == "NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY"


def _route_records(
    query: DriftQuery, body: Any, gaps: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    observed, parsed_gaps = normalize_mappings(
        body,
        path_pattern=None,
        http_method=None,
        controller_pattern=None,
        context_id=None,
    )
    gaps.extend(parsed_gaps)
    incomplete = bool(parsed_gaps)
    routes = _explode_routes(observed)
    expectations = query.expected_routes or ()
    missing: list[dict[str, Any]] = []
    covered: set[tuple[str, str, str]] = set()
    for expected in expectations:
        match = next(
            (route for route in routes if _route_matches(route, expected)), None
        )
        if match is not None:
            covered.add(match["key"])
            continue
        if incomplete:
            continue
        identity = (
            f"{expected.context_id}:{expected.http_method}:{expected.path}"
            if expected.context_id
            else f"unscoped:{expected.http_method}:{expected.path}"
        )
        row = _record(
            "routes",
            identity,
            expected.context_id,
            expected.controller_class or "present",
            "absent",
            "observed",
            [],
        )
        if row is None:
            gaps.append("IDENTITY_NOT_SAFE")
            incomplete = True
            continue
        missing.append(row)
    unexpected: list[dict[str, Any]] = []
    closed = (
        query.expected_scope_complete
        and not incomplete
        and all(item.context_id is not None for item in expectations)
    )
    if closed:
        seen: set[tuple[str, str, str]] = set()
        for route in routes:
            if route["key"] in covered or route["key"] in seen:
                continue
            seen.add(route["key"])
            row = _record(
                "routes",
                f"{route['contextId']}:{route['method']}:{route['path']}",
                route["contextId"],
                "absent",
                "present",
                "observed",
                [],
            )
            if row is None:
                gaps.append("IDENTITY_NOT_SAFE")
                incomplete = True
                continue
            unexpected.append(row)
    return missing, unexpected, incomplete


def _explode_routes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for row in rows:
        methods = row["httpMethods"] or ["ANY"]
        for path in row["paths"]:
            if not isinstance(path, str):
                continue
            for method in methods:
                routes.append(
                    {
                        "key": (row["contextId"], method, path),
                        "contextId": row["contextId"],
                        "method": method,
                        "path": path,
                        "controller": row["controllerClass"],
                    }
                )
    return routes


def _route_matches(route: dict[str, Any], expected: ExpectedRoute) -> bool:
    if expected.context_id is not None and route["contextId"] != expected.context_id:
        return False
    if route["path"] != expected.path:
        return False
    if route["method"] not in ("ANY", expected.http_method):
        return False
    return not (
        expected.controller_class and route["controller"] != expected.controller_class
    )


def _migration_record(
    query: DriftQuery, body: Any | None, gaps: list[str]
) -> tuple[list[dict[str, Any]], bool]:
    expected = query.expected_migration
    if expected is None:
        return [], False
    if expected.engine == "liquibase":
        gaps.append("MIGRATION_VERSION_UNSUPPORTED")
        if body is None:
            gaps.append("MIGRATION_ENDPOINT_UNOBSERVED")
        return [], True
    if body is None:
        gaps.append("MIGRATION_ENDPOINT_UNOBSERVED")
        return [], True
    rows, parsed_gaps, _visibility = normalize_migrations(
        body, None, MigrationQuery(engine="flyway")
    )
    gaps.extend(parsed_gaps)
    selected = [
        row
        for row in rows
        if row["engine"] == "flyway"
        and (expected.context_id is None or row["contextId"] == expected.context_id)
        and (
            expected.migration_bean is None
            or row["migrationBean"] == expected.migration_bean
        )
    ]
    groups: dict[tuple[str | None, str], list[dict[str, Any]]] = {}
    for row in selected:
        groups.setdefault((row["contextId"], row["migrationBean"]), []).append(row)
    obscured = any(gap in _FLYWAY_GAPS for gap in parsed_gaps)
    if len(groups) > 1:
        gaps.append("MIGRATION_SCOPE_AMBIGUOUS")
        return [], True
    if not groups:
        if obscured:
            gaps.append("MIGRATION_VERSION_UNOBSERVED")
            return [], True
        identity = (
            f"{expected.context_id or 'unscoped'}:{expected.migration_bean or 'flyway'}"
        )
        absent = _record(
            "migrations",
            identity,
            expected.context_id,
            expected.version,
            "absent",
            "observed",
            [],
        )
        if absent is None:
            gaps.append("IDENTITY_NOT_SAFE")
            return [], True
        return [absent], False
    context_id, bean_name = next(iter(groups))
    installed, install_state = _installed_version(groups[(context_id, bean_name)])
    if install_state == "unknown" or (install_state == "ok" and installed is None):
        gaps.append("MIGRATION_ORDER_UNKNOWN")
        return [], True
    actual = installed if install_state == "ok" and installed is not None else "absent"
    if actual == expected.version:
        return [], obscured
    diff = _record(
        "migrations",
        f"{context_id or 'unscoped'}:{bean_name}",
        context_id if isinstance(context_id, str) else None,
        expected.version,
        actual,
        "observed",
        [],
    )
    if diff is None:
        gaps.append("IDENTITY_NOT_SAFE")
        return [], True
    return [diff], obscured


def _installed_version(rows: list[dict[str, Any]]) -> tuple[str | None, str]:
    ranked: list[tuple[int, str]] = []
    saw_unranked = False
    for row in rows:
        if row.get("rawState") not in {
            "SUCCESS",
            "OUT_OF_ORDER",
            "MISSING_SUCCESS",
            "FUTURE_SUCCESS",
        } or not isinstance(row.get("version"), str):
            continue
        rank = row.get("orderExecuted")
        if type(rank) is not int:
            saw_unranked = True
            continue
        ranked.append((rank, row["version"]))
    if saw_unranked:
        return None, "unknown"
    if not ranked:
        return None, "absent"
    top = max(rank for rank, _version in ranked)
    winners = [version for rank, version in ranked if rank == top]
    if len(set(winners)) != 1:
        return None, "unknown"
    return winners[0], "ok"


def _profiles(
    expected: tuple[str, ...], body: Any | None, gaps: list[str]
) -> tuple[dict[str, Any], bool, bool]:
    safe_expected, expected_unsafe = _profile_tokens(expected)
    if body is None:
        gaps.append("PROFILE_UNOBSERVED")
        return (
            {"expected": safe_expected, "actual": None, "state": "unknown"},
            True,
            False,
        )
    if not isinstance(body, dict) or not isinstance(body.get("activeProfiles"), list):
        gaps.append("ENV_PROFILES_UNOBSERVED")
        return (
            {"expected": safe_expected, "actual": None, "state": "unknown"},
            True,
            False,
        )
    actual, actual_unsafe = _profile_tokens(tuple(body["activeProfiles"]))
    override = _environment_override(body)
    if expected_unsafe or actual_unsafe:
        gaps.append("PROFILE_TOKEN_UNSAFE")
        return (
            {"expected": safe_expected, "actual": None, "state": "unknown"},
            True,
            False,
        )
    state = "match" if set(safe_expected) == set(actual) else "mismatch"
    return (
        {"expected": safe_expected, "actual": actual, "state": state},
        False,
        override and state == "mismatch",
    )


def _profile_tokens(values: tuple[Any, ...]) -> tuple[list[str], bool]:
    safe: list[str] = []
    unsafe = False
    for value in values:
        if isinstance(value, str) and _PROFILE.fullmatch(value):
            safe.append(value)
        else:
            unsafe = True
    return sorted(set(safe)), unsafe


def _environment_override(body: dict[str, Any]) -> bool:
    single = body.get("property")
    if (
        isinstance(single, dict)
        and single.get("source") == "systemEnvironment"
        and _visible_scalar(single.get("value"))
    ):
        return True
    sources = body.get("propertySources")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        properties = source.get("properties")
        if (
            not isinstance(properties, dict)
            or "spring.profiles.active" not in properties
        ):
            continue
        raw_name = source.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        winning = "systemenvironment" in name.casefold().replace("_", "")
        spec = properties["spring.profiles.active"]
        raw = spec.get("value") if isinstance(spec, dict) else spec
        return winning and _visible_scalar(raw)
    return False


def _visible_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return (
            bool(value)
            and not any(char.isspace() for char in value)
            and set(value) != {"*"}
        )
    return type(value) in (int, bool)


def compare_drift(
    query: DriftQuery,
    *,
    beans: ParsedObservation | None,
    mappings_body: Any | None,
    flyway_body: Any | None,
    migration_observed: bool,
    env_body: Any | None,
    catalog: SourceCatalog,
) -> dict[str, Any]:
    gaps: list[str] = []
    missing: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    incomplete = False
    if query.expected_beans is not None:
        if beans is None:
            gaps.append("BEANS_UNOBSERVED")
            incomplete = True
        else:
            bean_missing, bean_unexpected, bean_incomplete = _bean_records(
                query, beans, catalog, gaps
            )
            missing.extend(bean_missing)
            unexpected.extend(bean_unexpected)
            incomplete = incomplete or bean_incomplete
    if query.expected_routes is not None:
        if mappings_body is None:
            gaps.append("MAPPINGS_UNOBSERVED")
            incomplete = True
        else:
            route_missing, route_unexpected, route_incomplete = _route_records(
                query, mappings_body, gaps
            )
            missing.extend(route_missing)
            unexpected.extend(route_unexpected)
            incomplete = incomplete or route_incomplete
    override = False
    profile = None
    if query.expected_migration is not None:
        body = (
            flyway_body
            if query.expected_migration.engine == "flyway"
            else ({} if migration_observed else None)
        )
        migration_rows, migration_incomplete = _migration_record(query, body, gaps)
        missing.extend(migration_rows)
        incomplete = incomplete or migration_incomplete
    if query.expected_profiles is not None:
        profile, profile_incomplete, override = _profiles(
            query.expected_profiles, env_body, gaps
        )
        incomplete = incomplete or profile_incomplete
    return {
        "missing": _sort(missing),
        "unexpected": _sort(unexpected),
        "profile": profile,
        "gaps": sorted(set(gaps)),
        "incomplete": incomplete or bool(gaps),
        "environment_override": override,
    }
