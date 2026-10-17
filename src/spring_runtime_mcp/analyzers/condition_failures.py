"""Evidence-only source/type correlation and multihop condition attribution."""

from typing import Literal

from ..contracts.definitions import glob_matches
from ..models.bean_graph import BeanGraph, ContextKey
from ..models.common import AnalysisBudget, AnalysisLimit, Fact
from ..models.conditions import (
    BlockingEdge,
    Condition,
    ConditionReport,
    FailureAnalysis,
)
from ..models.queries import InspectBeansQuery
from ..models.source_catalog import SourceCatalog, SourceProducer


def is_assignable(
    actual_type: str,
    expected_type: str,
    catalog: SourceCatalog,
    *,
    budget: AnalysisBudget,
) -> Fact[bool]:
    if actual_type == expected_type:
        return Fact("known", True, None)
    pending: list[tuple[str, frozenset[str]]] = [(actual_type, frozenset())]
    seen: set[str] = set()
    incomplete = False
    while pending:
        current, ancestors = pending.pop()
        budget.consume()
        if current == expected_type:
            return Fact("known", True, None)
        if current in ancestors:
            incomplete = True
            continue
        if current in seen:
            continue
        seen.add(current)
        fact = catalog.types.get(current)
        if fact is None:
            incomplete = True
            continue
        pending.extend(
            (parent, ancestors | {current}) for parent in fact.direct_supertypes
        )
    if incomplete or expected_type not in catalog.types:
        return Fact("unknown", None, "SOURCE_TYPE_UNOBSERVED")
    return Fact("known", False, None)


def producer_matches(
    producer: SourceProducer,
    query: InspectBeansQuery,
    catalog: SourceCatalog,
    *,
    budget: AnalysisBudget,
) -> bool:
    budget.consume()
    if query.context_id is not None and producer.context_id != query.context_id:
        return False
    if query.bean_name_pattern is not None and not any(
        glob_matches(query.bean_name_pattern, name)
        for name in (producer.bean_name, *producer.aliases)
    ):
        return False
    if query.package_prefix and not any(
        t.startswith(query.package_prefix.rstrip(".") + ".")
        for t in (
            producer.declared_type,
            producer.implementation_type or producer.declared_type,
        )
    ):
        return False
    if query.type_pattern:
        types = set(catalog.types) | {
            producer.declared_type,
            producer.implementation_type or producer.declared_type,
        }
        expected = sorted(t for t in types if glob_matches(query.type_pattern, t))
        return any(
            is_assignable(actual, target, catalog, budget=budget).value is True
            for actual in (
                producer.declared_type,
                producer.implementation_type or producer.declared_type,
            )
            for target in expected
        )
    return True


def analyze_missing_beans(
    query: InspectBeansQuery,
    graph: BeanGraph,
    report: ConditionReport,
    catalog: SourceCatalog,
    *,
    budget: AnalysisBudget,
) -> FailureAnalysis:
    if catalog.target_id != query.target_id:
        return FailureAnalysis(
            "unknown", (), (), (), False, ("SOURCE_TARGET_MISMATCH",)
        )
    selected: dict[str, Condition] = {}
    edges: list[BlockingEdge] = []
    paths: list[tuple[str, ...]] = []
    gaps = set(report.gaps) | set(catalog.gaps)
    if report.coverage != "complete":
        gaps.add("CONDITION_REPORT_INCOMPLETE")
    truncated = False
    contexts = {
        key.context_id: key
        for key in graph.contexts
        if key.target_id == query.target_id
    }

    def visible(context: ContextKey, search: str) -> tuple[ContextKey, ...] | None:
        if search == "unknown":
            return None
        result: list[ContextKey] = []
        seen: set[ContextKey] = set()
        current: ContextKey | None = context
        while current is not None:
            budget.consume()
            if current in seen:
                return None
            seen.add(current)
            item = graph.contexts.get(current)
            if item is None or not item.complete:
                return None
            if search != "PARENTS" or current != context:
                result.append(current)
            if search == "CURRENT":
                break
            if item.parent.state != "known":
                return None
            current = item.parent.value
        return tuple(result)

    def producer_roots(producer: SourceProducer) -> tuple[str, ...]:
        context = contexts.get(producer.context_id)
        if context is None:
            gaps.add("SOURCE_CONTEXT_UNOBSERVED")
            return ()
        result: list[str] = []
        for source in producer.condition_sources:
            result.extend(report.roots_by_source.get((context, source), ()))
        return tuple(result)

    def add_edge(parent: str, child: str, evidence: tuple[str, ...]) -> None:
        edge = BlockingEdge(parent, child, "CONDITION_BLOCKS_PRODUCER", evidence)
        if edge not in edges:
            edges.append(edge)

    def visit(
        node_id: str,
        path: tuple[str, ...],
        depth: int,
        active_producers: frozenset[str],
    ) -> bool:
        nonlocal truncated
        budget.consume()
        if node_id in path:
            gaps.add("CONDITION_CYCLE")
            return False
        if depth > min(query.condition_depth, budget.max_depth):
            truncated = True
            gaps.add("GRAPH_LIMIT_REACHED")
            return False
        node = report.nodes.get(node_id)
        if node is None:
            gaps.add("CONDITION_REFERENCE_UNOBSERVED")
            return False
        selected[node_id] = node
        chain = (*path, node_id)
        if not node.children_complete or node.reason_code == "CONDITION_PARSE_CONFLICT":
            gaps.add(node.reason_code)
            return False
        if node.reported_outcome != "notMatched":
            return False
        if node.operator in ("any", "all", "none"):
            children = [
                child
                for child in node.child_ids
                if report.nodes[child].reported_outcome == "notMatched"
            ]
            if node.operator == "none" or not children:
                gaps.add("CONDITION_NOT_A_MISSING_BEAN_CHAIN")
                return False
            results = [
                visit(child, chain, depth + 1, active_producers) for child in children
            ]
            return all(results) if node.operator == "any" else any(results)
        if node.reason_code in ("PROPERTY_NOT_FOUND", "PROPERTY_VALUE_MISMATCH"):
            paths.append(chain)
            return True
        if node.reason_code != "REQUIRED_BEAN_MISSING":
            gaps.add("UNRESOLVED_CONDITION_CAUSE")
            return False
        # A combined requirement does not prove which individual type/name
        # failed. Never invent one causal edge from an undifferentiated list.
        if len(node.required_names) + len(node.required_types) != 1:
            gaps.add("MULTI_BEAN_REQUIREMENTS_UNSUPPORTED")
            return False
        scope = visible(node.context, node.search)
        if scope is None:
            gaps.add("PARENT_CONTEXT_UNOBSERVED")
            return False
        # Reported missing state must not conflict with visible runtime facts.
        runtime_exists = False
        for bean in graph.beans.values():
            if bean.key.context not in scope:
                continue
            budget.consume()
            if any(
                name in (bean.key.canonical_name, *bean.aliases)
                for name in node.required_names
            ):
                runtime_exists = True
            if (
                bean.reported_type.state == "known"
                and bean.reported_type.value is not None
            ):
                for required in node.required_types:
                    fact = is_assignable(
                        bean.reported_type.value, required, catalog, budget=budget
                    )
                    runtime_exists = runtime_exists or fact.value is True
        if runtime_exists:
            gaps.add("CONDITION_RUNTIME_CONFLICT")
            return False
        # The official missing report is evidence even when unrelated runtime
        # types lack source inheritance facts; it is not inferred from enumeration.
        candidates: list[SourceProducer] = []
        for producer in catalog.producers:
            budget.consume()
            if producer.context_id not in {ctx.context_id for ctx in scope}:
                continue
            by_name = any(
                name in (producer.bean_name, *producer.aliases)
                for name in node.required_names
            )
            by_type = any(
                is_assignable(
                    producer.implementation_type or producer.declared_type,
                    required,
                    catalog,
                    budget=budget,
                ).value
                is True
                for required in node.required_types
            )
            if by_name or by_type:
                candidates.append(producer)
        if (
            len(candidates) != 1
            or len(node.required_names) + len(node.required_types) != 1
        ):
            gaps.add(
                "SOURCE_PRODUCER_AMBIGUOUS"
                if candidates
                else "SOURCE_FACTS_UNAVAILABLE"
            )
            return False
        producer = candidates[0]
        if producer.id in active_producers:
            gaps.add("CONDITION_CYCLE")
            return False
        roots = [
            rid
            for rid in producer_roots(producer)
            if report.nodes[rid].reported_outcome == "notMatched"
        ]
        if not roots:
            gaps.add("SOURCE_CONDITIONS_UNOBSERVED")
            return False
        parent_id = node_id
        next_path = chain
        next_depth = depth + 1
        if producer.factory_method:
            factory_id = f"factory_{producer.id}"
            factory = Condition(
                factory_id,
                contexts[producer.context_id],
                producer.configuration_class + "#" + producer.factory_method,
                None,
                "leaf",
                "notMatched",
                "notMatched",
                "unknown",
                (),
                (),
                Fact("notReported", None, "NOT_REPORTED"),
                (),
                True,
                "SOURCE_FACTORY_NOT_CREATED",
                producer.evidence_ids,
            )
            if next_depth > query.condition_depth:
                truncated = True
                gaps.add("GRAPH_LIMIT_REACHED")
                return False
            selected[factory_id] = factory
            add_edge(node_id, factory_id, (*node.evidence_ids, *producer.evidence_ids))
            parent_id = factory_id
            next_path = (*chain, factory_id)
            next_depth += 1
        result = False
        for root in roots:
            if next_depth <= query.condition_depth:
                add_edge(parent_id, root, (*node.evidence_ids, *producer.evidence_ids))
            result = (
                visit(root, next_path, next_depth, active_producers | {producer.id})
                or result
            )
        return result

    explained = False
    try:
        candidates = [
            p
            for p in catalog.producers
            if producer_matches(p, query, catalog, budget=budget)
        ]
        if not candidates:
            gaps.add("SOURCE_FACTS_UNAVAILABLE")
        if len(candidates) > 1:
            gaps.add("SOURCE_PRODUCER_AMBIGUOUS")
        for producer in candidates:
            roots = [
                rid
                for rid in producer_roots(producer)
                if report.nodes[rid].reported_outcome == "notMatched"
            ]
            if not roots:
                gaps.add("SOURCE_CONDITIONS_UNOBSERVED")
            for root in roots:
                explained = visit(root, (), 0, frozenset({producer.id})) or explained
    except AnalysisLimit:
        truncated = True
        gaps.add("GRAPH_LIMIT_REACHED")
    if graph.coverage != "complete":
        gaps.add("BEAN_INDEX_INCOMPLETE")
    state: Literal["explained", "partial", "unknown"] = (
        "explained"
        if explained and not gaps and not truncated
        else "partial"
        if selected
        else "unknown"
    )
    return FailureAnalysis(
        state,
        tuple(selected.values()),
        tuple(edges),
        tuple(paths),
        truncated,
        tuple(sorted(gaps)),
    )
