"""Deterministic alias-aware dependency resolution and breadth-first slices."""

from __future__ import annotations

from collections import deque

from ..models.bean_graph import (
    BeanGraph,
    BeanKey,
    ContextKey,
    DependencyEdge,
    GraphSlice,
    Resolution,
)
from ..models.common import AnalysisBudget, AnalysisLimit


def resolve_bean_reference(
    graph: BeanGraph, defining_context: ContextKey, requested_name: str
) -> Resolution:
    current: ContextKey | None = defining_context
    seen: set[ContextKey] = set()
    while current is not None:
        if current in seen:
            return Resolution("unresolved", (), "CONTEXT_CYCLE")
        seen.add(current)
        context = graph.contexts.get(current)
        if context is None:
            return Resolution("unresolved", (), "PARENT_CONTEXT_UNOBSERVED")
        if not context.complete:
            return Resolution("unresolved", (), "BEAN_INDEX_INCOMPLETE")
        candidates = graph.names.get(current, {}).get(requested_name, ())
        if len(candidates) > 1:
            return Resolution("ambiguous", candidates, "ALIAS_AMBIGUOUS")
        if candidates:
            return Resolution("resolved", candidates, None)
        if context.parent.state != "known":
            return Resolution("unresolved", (), "PARENT_CONTEXT_UNOBSERVED")
        current = context.parent.value
    return Resolution("unresolved", (), "NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY")


def build_dependency_graph(graph: BeanGraph, *, budget: AnalysisBudget) -> BeanGraph:
    forward: dict[BeanKey, list[DependencyEdge]] = {key: [] for key in graph.beans}
    reverse: dict[BeanKey, list[DependencyEdge]] = {key: [] for key in graph.beans}
    gaps = set(graph.gaps)
    try:
        for key, bean in sorted(graph.beans.items()):
            budget.consume()
            for requested in bean.requested_dependencies:
                budget.consume()
                resolution = resolve_bean_reference(graph, key.context, requested)
                edge = DependencyEdge(key, requested, resolution, bean.evidence_ids)
                forward[key].append(edge)
                if resolution.state == "resolved":
                    reverse[resolution.candidates[0]].append(edge)
                else:
                    gaps.add(resolution.reason_code or "DEPENDENCY_UNRESOLVED")
    except AnalysisLimit:
        gaps.add("GRAPH_LIMIT_REACHED")
    return BeanGraph(
        graph.contexts,
        graph.beans,
        graph.names,
        graph.canonical_to_names,
        {key: tuple(edges) for key, edges in forward.items()},
        {key: tuple(edges) for key, edges in reverse.items()},
        "partial" if gaps else "complete",
        tuple(sorted(gaps)),
    )


def visible_beans(
    graph: BeanGraph, start: ContextKey, *, budget: AnalysisBudget | None = None
) -> tuple[BeanKey, ...] | None:
    """List type-query candidates with the same shadowing rules as name lookup."""
    budget = budget or AnalysisBudget()
    current: ContextKey | None = start
    seen: set[ContextKey] = set()
    occupied: set[str] = set()
    selected: list[BeanKey] = []
    while current is not None:
        budget.consume()
        if current in seen:
            return None
        seen.add(current)
        context = graph.contexts.get(current)
        if context is None or not context.complete or context.parent.state != "known":
            return None
        for key in sorted(graph.beans):
            if key.context == current:
                budget.consume()
                if key.canonical_name not in occupied:
                    selected.append(key)
        # A local alias can also hide an ancestor's canonical name.
        occupied.update(graph.names.get(current, {}))
        current = context.parent.value
    return tuple(selected)


def slice_dependencies(
    graph: BeanGraph,
    roots: tuple[BeanKey, ...],
    *,
    direction: str,
    max_depth: int,
    budget: AnalysisBudget,
) -> GraphSlice:
    if direction not in (
        "dependencies",
        "dependents",
        "both",
    ) or not 0 <= max_depth <= min(16, budget.max_depth):
        raise ValueError("INVALID_GRAPH_QUERY")
    gaps = set(graph.gaps)
    selected: dict[tuple[BeanKey, str], tuple[DependencyEdge, int]] = {}
    cut = False
    # Separate traversals avoid interpreting a zig-zag in 'both' as a dependency chain.
    directions = ("dependencies", "dependents") if direction == "both" else (direction,)
    try:
        for traversal in directions:
            queue = deque((key, 0) for key in sorted(set(roots)))
            seen = set(roots)
            index = graph.forward if traversal == "dependencies" else graph.reverse
            while queue:
                key, depth = queue.popleft()
                budget.consume()
                edges = index.get(key, ())
                if depth >= max_depth:
                    # Cycles already inside the visited set do not imply a hidden closure.
                    if any(
                        (
                            edge.resolution.candidates[0]
                            if traversal == "dependencies"
                            and edge.resolution.state == "resolved"
                            else edge.from_key
                            if traversal == "dependents"
                            else None
                        )
                        not in seen
                        for edge in edges
                    ):
                        cut = True
                    continue
                for edge in edges:
                    budget.consume()
                    identity = (edge.from_key, edge.requested_name)
                    previous = selected.get(identity)
                    if previous is None or depth + 1 < previous[1]:
                        selected[identity] = (edge, depth + 1)
                    next_key = (
                        edge.resolution.candidates[0]
                        if traversal == "dependencies"
                        and edge.resolution.state == "resolved"
                        else edge.from_key
                        if traversal == "dependents"
                        else None
                    )
                    if next_key is not None and next_key not in seen:
                        seen.add(next_key)
                        queue.append((next_key, depth + 1))
    except AnalysisLimit:
        cut = True
    if cut:
        gaps.add("GRAPH_LIMIT_REACHED")
    edges_result = tuple(
        sorted(
            selected.values(),
            key=lambda pair: (pair[1], pair[0].from_key, pair[0].requested_name),
        )
    )
    return GraphSlice(tuple(sorted(set(roots))), edges_result, cut, tuple(sorted(gaps)))
