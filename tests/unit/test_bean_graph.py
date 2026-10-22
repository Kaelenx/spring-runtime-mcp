"""Offline regressions for alias and parent-scoped graph resolution."""

from spring_runtime_mcp.adapters.beans import normalize_beans
from spring_runtime_mcp.analyzers.bean_graph import (
    build_dependency_graph,
    resolve_bean_reference,
    slice_dependencies,
)
from spring_runtime_mcp.models.bean_graph import ContextKey
from spring_runtime_mcp.models.common import AnalysisBudget, ParsedObservation


def record(kind="example.Bean", dependencies=(), aliases=()):
    return {
        "type": kind,
        "aliases": list(aliases),
        "dependencies": list(dependencies),
        "scope": "singleton",
    }


def graph_for(contexts, budget=None):
    budget = budget or AnalysisBudget()
    # These unit fixtures intend contexts without a parentId to be known roots.
    contexts = {
        name: ({**value, "parentId": None} if "parentId" not in value else value)
        for name, value in contexts.items()
    }
    observation = ParsedObservation(
        "local",
        "snapshot",
        "observation",
        "2026-10-16T00:00:00Z",
        "beans",
        {"contexts": contexts},
        200,
        None,
        "actuatorV3",
    )
    return build_dependency_graph(
        normalize_beans(observation, budget=budget), budget=budget
    )


def key(graph, context, name):
    return next(
        bean
        for bean in graph.beans
        if bean.context.context_id == context and bean.canonical_name == name
    )


def fixture():
    return {
        "parent": {
            "beans": {
                "factory": record(),
                "datasource": record(dependencies=["factory"]),
                "repository": record(dependencies=["datasource"], aliases=["repo"]),
                "client": record("example.ParentClient"),
                "audit": record(dependencies=["client"]),
            }
        },
        "child": {
            "parentId": "parent",
            "beans": {
                "client": record("example.ChildClient"),
                "service": record(
                    dependencies=["repo", "client"], aliases=["services"]
                ),
                "controller": record(dependencies=["services"]),
            },
        },
    }


def test_alias_parent_shadowing_and_reverse():
    graph = graph_for(fixture())
    service = key(graph, "child", "service")
    repository = key(graph, "parent", "repository")
    resolved = {
        edge.requested_name: edge.resolution.candidates[0]
        for edge in graph.forward[service]
    }
    assert resolved["repo"] == repository
    assert resolved["client"] == key(graph, "child", "client")
    assert graph.forward[key(graph, "parent", "audit")][0].resolution.candidates == (
        key(graph, "parent", "client"),
    )
    assert graph.canonical_to_names[repository] == ("repo", "repository")
    sliced = slice_dependencies(
        graph,
        (service,),
        direction="dependencies",
        max_depth=3,
        budget=AnalysisBudget(),
    )
    assert any(
        edge.resolution.candidates == (key(graph, "parent", "factory"),) and depth == 3
        for edge, depth in sliced.edges
    )
    reverse = slice_dependencies(
        graph,
        (repository,),
        direction="dependents",
        max_depth=2,
        budget=AnalysisBudget(),
    )
    assert {edge.from_key.canonical_name for edge, _ in reverse.edges} == {
        "service",
        "controller",
    }
    assert not reverse.truncated


def test_parent_never_resolves_child_and_conflicting_alias_never_falls_back():
    contexts = fixture()
    contexts["child"]["beans"]["conflict"] = record(aliases=["repo"])
    contexts["child"]["beans"]["other"] = record(aliases=["repo"])
    graph = graph_for(contexts)
    child = ContextKey("local", "snapshot", "child")
    parent = ContextKey("local", "snapshot", "parent")
    assert resolve_bean_reference(graph, child, "repo").state == "ambiguous"
    assert (
        resolve_bean_reference(graph, parent, "service").reason_code
        == "NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY"
    )
    assert (
        resolve_bean_reference(
            graph, ContextKey("other", "snapshot", "child"), "repo"
        ).state
        == "unresolved"
    )


def test_missing_dependencies_not_empty_success():
    raw = record()
    del raw["dependencies"]
    graph = graph_for({"application": {"beans": {"bean": raw}}})
    assert graph.coverage == "partial"
    assert "DEPENDENCIES_NOT_REPORTED" in graph.gaps


def test_missing_parent_and_context_cycle_are_unresolved():
    graph = graph_for(
        {"a": {"parentId": "missing", "beans": {"a": record(dependencies=["b"])}}}
    )
    assert (
        graph.forward[key(graph, "a", "a")][0].resolution.reason_code
        == "PARENT_CONTEXT_UNOBSERVED"
    )
    graph = graph_for(
        {
            "a": {"parentId": "b", "beans": {"a": record(dependencies=["absent"])}},
            "b": {"parentId": "a", "beans": {}},
        }
    )
    assert (
        graph.forward[key(graph, "a", "a")][0].resolution.reason_code == "CONTEXT_CYCLE"
    )


def test_bfs_cycles_budgets_and_depth_are_bounded():
    graph = graph_for(
        {
            "application": {
                "beans": {
                    "a": record(dependencies=["b"]),
                    "b": record(dependencies=["a", "c"]),
                    "c": record(),
                }
            }
        }
    )
    root = key(graph, "application", "a")
    shallow = slice_dependencies(
        graph, (root,), direction="dependencies", max_depth=1, budget=AnalysisBudget()
    )
    assert shallow.truncated
    assert len(shallow.edges) == 1
    full = slice_dependencies(
        graph, (root,), direction="dependencies", max_depth=16, budget=AnalysisBudget()
    )
    assert len(full.edges) == 3
    assert not full.truncated
    limited = slice_dependencies(
        graph,
        (root,),
        direction="both",
        max_depth=3,
        budget=AnalysisBudget(max_visits=1),
    )
    assert limited.truncated
    malformed = graph_for(fixture(), AnalysisBudget(max_visits=2))
    assert malformed.coverage == "partial"


def test_omitted_parent_is_a_root_context_like_boot_reports_it():
    # Boot emits parentId only for child contexts; verified against a real
    # Boot 3.4.5 /actuator/beans payload. Treating the omission as unknown
    # made every single-context application permanently partial.
    observation = ParsedObservation(
        "local",
        "snapshot",
        "observation",
        "2026-10-16T00:00:00Z",
        "beans",
        {
            "contexts": {
                "application": {"beans": {"bean": record(dependencies=["missing"])}}
            }
        },
        200,
        None,
        "actuatorV3",
    )
    graph = build_dependency_graph(
        normalize_beans(observation, budget=AnalysisBudget()),
        budget=AnalysisBudget(),
    )
    context = ContextKey("local", "snapshot", "application")
    assert graph.contexts[context].parent.state == "known"
    assert graph.contexts[context].parent.value is None
    assert "PARENT_CONTEXT_UNOBSERVED" not in graph.gaps
    # The unresolved dependency is still reported, now with the real reason.
    assert (
        graph.forward[key(graph, "application", "bean")][0].resolution.reason_code
        == "NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY"
    )


def test_malformed_alias_does_not_claim_complete_lookup():
    raw = record()
    raw["aliases"] = "not-a-list"
    graph = graph_for({"application": {"beans": {"a": raw}}})
    assert (
        resolve_bean_reference(
            graph, ContextKey("local", "snapshot", "application"), "missing"
        ).reason_code
        == "BEAN_INDEX_INCOMPLETE"
    )
