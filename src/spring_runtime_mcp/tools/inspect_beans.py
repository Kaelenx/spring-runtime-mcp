"""Read-only Bean handler, independent of HTTP and MCP host implementations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..adapters.beans import normalize_beans
from ..adapters.conditions import normalize_conditions
from ..analyzers.bean_graph import build_dependency_graph, slice_dependencies
from ..analyzers.condition_failures import analyze_missing_beans, is_assignable
from ..contracts.definitions import glob_matches, validate_arguments
from ..diagnostics import _check, envelope, error, failed, partial, safe_code
from ..services import Services as Services
from .support import tool_handler
from ..models.common import (
    AnalysisBudget,
    AnalysisLimit,
    NormalizationError,
)
from ..models.conditions import FailureAnalysis
from ..models.queries import InspectBeansQuery
from ..models.source_catalog import SourceCatalog
from ..policy.sanitization import MARKER, SanitizerPipeline
from ..transport.response_gate import EndpointReadError
from ..transport.responses import serialize_response

"""Fixture substitution is provenance, not missing Spring evidence.

An absent operator fact catalog explains unknown producers without marking the
runtime Bean evidence itself incomplete.
"""
_PROVENANCE_GAPS = frozenset({"FIXTURE_SUBSTITUTED", "SOURCE_CATALOG_UNPROVIDED"})


def _analysis_catalog(catalog: SourceCatalog) -> SourceCatalog:
    kept = tuple(gap for gap in catalog.gaps if gap not in _PROVENANCE_GAPS)
    if kept == catalog.gaps:
        return catalog
    return SourceCatalog(
        catalog.target_id,
        catalog.source_revision,
        catalog.types,
        catalog.producers,
        kept,
    )


def _page(
    result: dict[str, Any], query: InspectBeansQuery, services: Services
) -> dict[str, Any]:
    items = result["data"]["items"]
    count = min(len(items), query.max_results)
    while True:
        page = deepcopy(result)
        page["data"]["items"] = deepcopy(items[:count])
        remaining = items[count:]
        credits = 50 - count
        associations_cut = False
        for item in page["data"]["items"]:
            for field in ("aliases", "dependencies", "dependents"):
                values = item[field]
                keep = min(len(values), credits)
                associations_cut |= keep < len(values)
                item[field] = values[:keep]
                credits -= keep
        if associations_cut:
            page["truncated"] = True
            page["truncationReasons"] = sorted(
                set(page["truncationReasons"] + ["ASSOCIATION_LIMIT"])
            )[:8]
            partial(page, ["ASSOCIATION_LIMIT"])
            page["nextAction"]["filterSuggestions"] = [
                {"parameter": "maxResults", "value": 1}
            ]
        page["nextCursor"] = "x" * 64 if remaining else None
        if remaining:
            page["truncated"] = True
            page["truncationReasons"] = sorted(
                set(page["truncationReasons"] + ["MAX_RESULTS"])
            )[:8]
            page["nextAction"] = {
                "kind": "page",
                "reasonCode": "MORE_RESULTS",
                "filterSuggestions": [],
            }
        page = services.sanitizer.sanitize(page)
        try:
            serialize_response(page, "\0" * 128, sanitizer=services.sanitizer)
            break
        except NormalizationError as exc:
            if exc.code != "RESPONSE_TOO_LARGE":
                raise
            if count > 1:
                count //= 2
                continue
            # Keep the root fact and explicitly discard oversized associations.
            if count == 1 and any(
                page["data"]["items"][0][k]
                for k in ("dependencies", "dependents", "aliases")
            ):
                for key in ("dependencies", "dependents", "aliases"):
                    items[0][key] = []
            elif page["data"]["missingBeanAnalysis"]["conditions"]:
                result["data"]["missingBeanAnalysis"] = {
                    "state": "partial",
                    "conditions": [],
                    "blockingPaths": [],
                }
            else:
                return failed("RESPONSE_TOO_LARGE", query.target_id)
            partial(result, ["RESPONSE_BUDGET"])
            result["truncated"] = True
            result["truncationReasons"] = ["RESPONSE_BUDGET"]
    if remaining:
        deadline = (
            services.snapshots.deadline(query.cursor, query) if query.cursor else None
        )
        continuation = deepcopy(result)
        continuation["data"]["items"] = remaining
        page["nextCursor"] = services.snapshots.put(
            services.sanitizer.sanitize(continuation),
            query,
            sanitizer=services.sanitizer,
            expires_at=deadline,
        )
        serialize_response(page, "\0" * 128, sanitizer=services.sanitizer)
    return page


@tool_handler
async def inspect_beans(
    arguments: Mapping[str, Any], services: Services
) -> dict[str, Any]:
    """Return a sanitized contract envelope; no caller-controlled network access."""
    request_target = "local"
    try:
        query = validate_arguments(arguments)
        request_target = query.target_id
        if (
            query.target_id not in services.targets
            or services.catalog.target_id != query.target_id
        ):
            raise NormalizationError("TARGET_NOT_REGISTERED")
        if query.cursor:
            return _page(
                services.snapshots.get(
                    query.cursor, query, sanitizer=services.sanitizer
                ),
                query,
                services,
            )
        result = envelope(query.target_id)
        snapshot = uuid4().hex
        budget = AnalysisBudget(
            deadline_monotonic=time.monotonic() + 15, monotonic=time.monotonic
        )
        observation = await asyncio.wait_for(
            services.read(query.target_id, "beans", snapshot_id=snapshot),
            timeout=15,
        )
        _check(observation, query.target_id, snapshot, "beans")
        graph = build_dependency_graph(
            normalize_beans(observation, budget=budget), budget=budget
        )
        result["source"].update(
            snapshotId=snapshot,
            endpointIds=["beans"],
            observedAt=datetime.fromisoformat(
                observation.observed_at.replace("Z", "+00:00")
            )
            .astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        )
        clean = services.sanitizer.identifier
        provenance = [gap for gap in services.catalog.gaps if gap in _PROVENANCE_GAPS]
        gaps = list(
            dict.fromkeys(
                (
                    *graph.gaps,
                    *(
                        gap
                        for gap in services.catalog.gaps
                        if gap not in _PROVENANCE_GAPS
                    ),
                )
            )
        )
        if graph.coverage != "complete":
            gaps.append("BEAN_GRAPH_INCOMPLETE")
        items: list[dict[str, Any]] = []
        graph_exhausted = False
        association_credits = 50
        for key, bean in sorted(graph.beans.items()):
            # The bounded normalized index still contains valid scalar facts
            # after traversal fuel runs out. Preserve them for filtering/paging.
            if not graph_exhausted:
                try:
                    budget.consume()
                except AnalysisLimit as exc:
                    if exc.code != "GRAPH_LIMIT_REACHED":
                        raise
                    graph_exhausted = True
                    gaps.append("GRAPH_LIMIT_REACHED")
                    result["truncated"] = True
            safe_names = tuple(
                clean(name) for name in (key.canonical_name, *bean.aliases)
            )
            safe_context = clean(key.context.context_id)
            safe_type = clean(bean.reported_type.value)
            if MARKER in (*safe_names, safe_context, safe_type):
                gaps.append("REDACTED_IDENTIFIERS")
            if query.context_id and safe_context != query.context_id:
                continue
            if query.bean_name_pattern and not any(
                isinstance(name, str)
                and name != MARKER
                and glob_matches(query.bean_name_pattern, name)
                for name in safe_names
            ):
                continue
            if bean.reported_type.state != "known":
                gaps.append("TYPE_NOT_REPORTED")
            if bean.scope.state != "known":
                gaps.append("SCOPE_NOT_REPORTED")
            # resource is optional for programmatic beans; a null value is a
            # reported fact, not missing coverage.
            actual = bean.reported_type.value if safe_type != MARKER else None
            if query.package_prefix and not (
                actual and (actual.startswith(query.package_prefix.rstrip(".") + "."))
            ):
                continue
            if query.type_pattern and not (
                actual and glob_matches(query.type_pattern, actual)
            ):
                if not actual or "*" in query.type_pattern or "?" in query.type_pattern:
                    continue
                try:
                    assignable = is_assignable(
                        actual, query.type_pattern, services.catalog, budget=budget
                    )
                except AnalysisLimit as exc:
                    if exc.code != "GRAPH_LIMIT_REACHED":
                        raise
                    graph_exhausted = True
                    gaps.extend(["GRAPH_LIMIT_REACHED", "SOURCE_TYPE_UNOBSERVED"])
                    result["truncated"] = True
                    continue
                if assignable.state != "known":
                    gaps.append("SOURCE_TYPE_UNOBSERVED")
                if assignable.value is not True:
                    continue
            context = graph.contexts[key.context]
            item: dict[str, Any] = {
                "id": bean.id,
                "endpointId": "beans",
                "observationId": clean(observation.observation_id, max_length=96),
                "contextId": clean(key.context.context_id),
                "parentContextId": clean(context.parent.value.context_id)
                if context.parent.value
                else None,
                "name": clean(key.canonical_name),
                "type": clean(actual),
                "scope": clean(bean.scope.value, max_length=64),
                "resource": services.sanitizer.resource(bean.resource.value),
                "aliases": [
                    clean(a) for a in bean.aliases[: min(16, association_credits)]
                ],
                "producer": None,
                "dependencies": [],
                "dependents": [],
            }
            association_credits -= len(item["aliases"])
            if len(bean.aliases) > len(item["aliases"]):
                gaps.append("ASSOCIATION_LIMIT")
                result["truncated"] = True
            producers = [
                p
                for p in services.catalog.producers
                if p.context_id == key.context.context_id
                and (
                    p.bean_name == key.canonical_name or key.canonical_name in p.aliases
                )
                and actual in (p.declared_type, p.implementation_type)
            ]
            if len(producers) == 1:
                producer = producers[0]
                item["producer"] = {
                    "kind": "factoryMethod"
                    if producer.factory_method
                    else "configuration",
                    "configurationClass": clean(producer.configuration_class),
                    "methodName": clean(producer.factory_method),
                    "evidenceLevel": "correlated",
                }
            for direction in ("dependencies", "dependents"):
                if query.direction not in (direction, "both"):
                    continue
                if graph_exhausted:
                    continue
                if association_credits == 0:
                    index = (
                        graph.forward if direction == "dependencies" else graph.reverse
                    )
                    if index.get(key):
                        gaps.append("ASSOCIATION_LIMIT")
                        result["truncated"] = True
                    continue
                sliced = slice_dependencies(
                    graph,
                    (key,),
                    direction=direction,
                    max_depth=query.max_depth,
                    budget=budget,
                )
                gaps.extend(sliced.gaps)
                kept_edges = min(16, association_credits)
                if sliced.truncated or len(sliced.edges) > kept_edges:
                    gaps.append("ASSOCIATION_LIMIT")
                    result["truncated"] = True
                association_credits -= min(len(sliced.edges), kept_edges)
                for edge, depth in sliced.edges[:kept_edges]:
                    target = (
                        edge.resolution.candidates[0]
                        if edge.resolution.state == "resolved"
                        else None
                    )
                    item[direction].append(
                        {
                            "fromId": graph.beans[edge.from_key].id,
                            "toId": graph.beans[target].id if target else None,
                            "requestedName": clean(edge.requested_name),
                            "targetContextId": clean(target.context.context_id)
                            if target
                            else None,
                            "targetName": clean(target.canonical_name)
                            if target
                            else None,
                            "resolution": edge.resolution.state,
                            "depth": depth,
                        }
                    )
            items.append(item)
        result["data"]["items"] = items
        if not items and any(
            (query.bean_name_pattern, query.type_pattern, query.package_prefix)
        ):
            try:
                conditions = await asyncio.wait_for(
                    services.read(query.target_id, "conditions", snapshot_id=snapshot),
                    timeout=15,
                )
                _check(conditions, query.target_id, snapshot, "conditions")
                result["source"]["endpointIds"].append("conditions")
                assert conditions.body is not None
                contexts = conditions.body.get("contexts", {})
                if not isinstance(contexts, dict):
                    raise NormalizationError("CONTEXT_HIERARCHY_CONFLICT")
                for context_key, context in graph.contexts.items():
                    raw = contexts.get(context_key.context_id)
                    expected = (
                        context.parent.value.context_id
                        if context.parent.value
                        else None
                    )
                    if not isinstance(raw, dict) or raw.get("parentId") != expected:
                        raise NormalizationError("CONTEXT_HIERARCHY_CONFLICT")
                report = normalize_conditions(conditions, budget=budget)
                analysis = analyze_missing_beans(
                    query,
                    graph,
                    report,
                    _analysis_catalog(services.catalog),
                    budget=budget,
                )
                result["data"]["missingBeanAnalysis"] = _failure(
                    analysis, services.sanitizer
                )
                gaps.extend(analysis.gaps)
                if analysis.state in ("partial", "unknown"):
                    gaps.append("CONDITION_EVIDENCE_INCOMPLETE")
                if analysis.truncated or len(analysis.conditions) > 16:
                    result["truncated"] = True
                    gaps.append("CONDITION_LIMIT")
            except NormalizationError as exc:
                result["data"]["missingBeanAnalysis"]["state"] = "unknown"
                gaps.append(safe_code(exc.code))
                result["errors"].append(
                    error(
                        exc.code,
                        "conditions",
                        exc.http_status if isinstance(exc, EndpointReadError) else None,
                    )
                )
        incomplete_evidence = {
            "SOURCE_TYPE_UNOBSERVED",
            "REDACTED_IDENTIFIERS",
            "TYPE_NOT_REPORTED",
            "SCOPE_NOT_REPORTED",
        }
        result["totalMatched"] = None if incomplete_evidence & set(gaps) else len(items)
        partial(result, gaps)
        if provenance:
            result["coverage"]["gaps"] = sorted(
                set(result["coverage"]["gaps"] + provenance)
            )[:10]
        if result["truncated"]:
            cause_order = (
                "RESPONSE_BUDGET",
                "GRAPH_LIMIT_REACHED",
                "CONDITION_LIMIT",
                "ASSOCIATION_LIMIT",
                "MAX_RESULTS",
            )
            derived = [
                code
                for code in cause_order
                if code in gaps or code in result["truncationReasons"]
            ][:8]
            if not derived:
                derived = list(result["truncationReasons"]) or ["RESPONSE_BUDGET"]
            result["truncationReasons"] = derived
        return _page(services.sanitizer.sanitize(result), query, services)
    except NormalizationError as exc:
        return failed(
            exc.code,
            request_target,
            endpoint=exc.endpoint_id if isinstance(exc, EndpointReadError) else None,
            http_status=exc.http_status if isinstance(exc, EndpointReadError) else None,
        )
    except TimeoutError:
        return failed("TARGET_TIMEOUT", request_target)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        return failed("MALFORMED_EVIDENCE", request_target)
    except Exception:
        return failed("INTERNAL_ERROR", request_target)


def _failure(analysis: FailureAnalysis, sanitizer: SanitizerPipeline) -> dict[str, Any]:
    retained = {node.id for node in analysis.conditions[:16]}
    nodes = []
    for node in analysis.conditions[:16]:
        children = list(
            dict.fromkeys(
                (
                    *node.child_ids,
                    *(e.to_id for e in analysis.blocking_edges if e.from_id == node.id),
                )
            )
        )
        nodes.append(
            {
                "id": node.id,
                "configurationClass": sanitizer.identifier(node.source.split("#")[0]),
                "conditionType": sanitizer.identifier(node.condition_type),
                "operator": node.operator,
                "outcome": node.derived_outcome,
                "reasonCode": node.reason_code,
                "childIds": [c for c in children if c in retained][:16],
                "requiredBeanIds": [],
                "childrenComplete": node.children_complete
                and all(c in retained for c in children)
                and len(children) <= 16,
            }
        )
    cut = len(analysis.conditions) > 16
    return {
        "state": "partial" if cut else analysis.state,
        "conditions": nodes,
        "blockingPaths": [
            list(p)
            for p in analysis.blocking_paths
            if len(p) <= 16 and all(c in retained for c in p)
        ][:8],
    }
