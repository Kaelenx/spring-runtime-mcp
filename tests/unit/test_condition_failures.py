"""Offline regressions for explicit source evidence and condition causality."""

import copy
import json
from pathlib import Path

import pytest

from spring_runtime_mcp.adapters.beans import normalize_beans
from spring_runtime_mcp.adapters.conditions import combine, normalize_conditions
from spring_runtime_mcp.adapters.source_catalog import load_source_catalog
from spring_runtime_mcp.analyzers.condition_failures import (
    analyze_missing_beans,
    is_assignable,
)
from spring_runtime_mcp.models.common import (
    AnalysisBudget,
    NormalizationError,
    ParsedObservation,
)
from spring_runtime_mcp.models.queries import InspectBeansQuery
from spring_runtime_mcp.models.source_catalog import SourceCatalog, TypeFact

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bean_graph"


def fixture(path):
    return json.loads((FIXTURES / path).read_text())


def observation(body, endpoint="conditions"):
    return ParsedObservation(
        "local", "snapshot", "observation", "2026-10-16T00:00:00Z", endpoint, body
    )


def setup_case(case="stripe-missing", depth=4):
    budget = AnalysisBudget()
    graph = normalize_beans(
        observation(fixture("parent-child/beans.json"), "beans"), budget=budget
    )
    report = normalize_conditions(
        observation(fixture(case + "/conditions.json")), budget=budget
    )
    catalog = load_source_catalog(
        fixture(case + "/source-catalog.json"), target_id="local", budget=budget
    )
    query = InspectBeansQuery(
        type_pattern="example.PaymentGateway",
        context_id="application",
        condition_depth=depth,
    )
    return query, graph, report, catalog, budget


def test_interface_to_factory_to_property_two_hops():
    query, graph, report, catalog, budget = setup_case()
    result = analyze_missing_beans(query, graph, report, catalog, budget=budget)
    assert result.state == "explained", result.gaps
    assert len(result.blocking_paths) == 1
    nodes = {node.id: node for node in result.conditions}
    path = result.blocking_paths[0]
    assert [nodes[i].reason_code for i in path] == [
        "REQUIRED_BEAN_MISSING",
        "SOURCE_FACTORY_NOT_CREATED",
        "PROPERTY_NOT_FOUND",
    ]
    assert nodes[path[-1]].property_key.value == "stripe.enabled"
    assert len(result.blocking_edges) == 2
    assert all(not node.child_ids for node in result.conditions), (
        "Causal and Boolean edges must remain separate"
    )
    assert "message" not in repr(result)


def test_factory_method_sources_exact_and_missing_catalog():
    query, graph, report, catalog, budget = setup_case("factory-method")
    result = analyze_missing_beans(query, graph, report, catalog, budget=budget)
    assert result.state == "explained", result.gaps
    empty = SourceCatalog("local", None, {}, (), ())
    result = analyze_missing_beans(query, graph, report, empty, budget=AnalysisBudget())
    assert result.state == "unknown"
    assert "SOURCE_FACTS_UNAVAILABLE" in result.gaps


def test_depth_is_causal_edges_not_type_lookup():
    for depth, expected in [(1, "partial"), (2, "explained")]:
        query, graph, report, catalog, budget = setup_case(depth=depth)
        result = analyze_missing_beans(query, graph, report, catalog, budget=budget)
        assert result.state == expected
        assert result.truncated is (depth == 1)
        if depth == 1:
            assert "GRAPH_LIMIT_REACHED" in result.gaps


def test_nested_tree_and_unknown_are_conservative():
    report = normalize_conditions(
        observation(fixture("nested-conditions/conditions.json")),
        budget=AnalysisBudget(),
    )
    roots = [report.nodes[ids[0]] for ids in report.roots_by_source.values()]
    root = next(node for node in roots if node.operator == "any")
    assert root.derived_outcome == "notMatched"
    assert root.children_complete
    assert {report.nodes[i].operator for i in root.child_ids} == {"all", "none"}
    payload = fixture("nested-conditions/conditions.json")
    entries = payload["contexts"]["application"]["negativeMatches"]
    entry = next(iter(entries.values()))["notMatched"][0]
    entry["message"] = entry["message"].replace(
        "did not find property 'enabled'", "unrecognized text"
    )
    unknown = normalize_conditions(observation(payload), budget=AnalysisBudget())
    assert unknown.coverage == "partial"
    assert all(not hasattr(node, "message") for node in unknown.nodes.values())


@pytest.mark.parametrize(
    "operator,values,complete,expected",
    [
        ("any", ("matched", "unknown"), False, "matched"),
        ("any", ("notMatched",), False, "unknown"),
        ("all", ("notMatched", "unknown"), False, "notMatched"),
        ("all", ("matched",), False, "unknown"),
        ("none", ("matched", "unknown"), False, "notMatched"),
        ("none", ("notMatched",), False, "unknown"),
        ("none", ("notMatched",), True, "matched"),
    ],
)
def test_three_valued_nested_logic(operator, values, complete, expected):
    assert combine(operator, values, complete) == expected


def test_reported_matches_authoritative_and_mismatch_partial():
    payload = fixture("stripe-missing/conditions.json")
    report = normalize_conditions(observation(payload), budget=AnalysisBudget())
    fallback = next(
        node
        for node in report.nodes.values()
        if node.source == "example.FallbackConfiguration"
    )
    assert fallback.reported_outcome == fallback.derived_outcome == "matched"
    payload["contexts"]["application"]["positiveMatches"]["example.Bad"] = [
        {
            "condition": "OnPropertyCondition",
            "message": "@ConditionalOnProperty (stripe.enabled=true) did not find property 'enabled'",
        }
    ]
    conflict = normalize_conditions(observation(payload), budget=AnalysisBudget())
    assert "CONDITION_PARSE_CONFLICT" in conflict.gaps
    bad = next(node for node in conflict.nodes.values() if node.source == "example.Bad")
    assert bad.reported_outcome == "matched"


def test_assignability_multihop_missing_and_cycle():
    catalog = SourceCatalog(
        "local",
        None,
        {
            "A": TypeFact("A", ("B",)),
            "B": TypeFact("B", ("I",)),
            "I": TypeFact("I", ()),
        },
        (),
        (),
    )
    assert is_assignable("A", "I", catalog, budget=AnalysisBudget()).value is True
    assert is_assignable("I", "A", catalog, budget=AnalysisBudget()).value is False
    assert (
        is_assignable("Missing", "I", catalog, budget=AnalysisBudget()).state
        == "unknown"
    )
    cyclic = SourceCatalog(
        "local",
        None,
        {
            "A": TypeFact("A", ("B",)),
            "B": TypeFact("B", ("A",)),
            "I": TypeFact("I", ()),
        },
        (),
        (),
    )
    assert is_assignable("A", "I", cyclic, budget=AnalysisBudget()).state == "unknown"


@pytest.mark.parametrize("mutation", ["target", "path", "extra", "line", "duplicate"])
def test_source_catalog_rejects_unsafe_or_ambiguous_metadata(mutation):
    payload = fixture("stripe-missing/source-catalog.json")
    if mutation == "target":
        payload["targetId"] = "another"
    elif mutation == "path":
        payload["producers"][0]["sourceFile"] = "../outside.java"
    elif mutation == "extra":
        payload["producers"][0]["command"] = "do something"
    elif mutation == "line":
        payload["producers"][0]["sourceLine"] = True
    else:
        payload["types"].append(copy.deepcopy(payload["types"][0]))
    with pytest.raises(NormalizationError):
        load_source_catalog(payload, target_id="local", budget=AnalysisBudget())


@pytest.mark.parametrize(
    "search,expected",
    [("all", "explained"), ("parents", "explained"), ("current", "partial")],
)
def test_search_strategy_limits_missing_bean_producer_scope(search, expected):
    beans = fixture("parent-child/beans.json")
    payload = fixture("stripe-missing/conditions.json")
    catalog_payload = fixture("stripe-missing/source-catalog.json")
    application = payload["contexts"]["application"]
    gateway = application["negativeMatches"]["example.StripePaymentGateway"][
        "notMatched"
    ][0]
    gateway["message"] = gateway["message"].replace(
        "SearchStrategy: all", "SearchStrategy: " + search
    )
    factory = application["negativeMatches"].pop(
        "example.StripeConfiguration#stripeClient"
    )
    payload["contexts"]["infrastructure"]["negativeMatches"][
        "example.StripeConfiguration#stripeClient"
    ] = factory
    catalog_payload["producers"][1]["contextId"] = "infrastructure"
    budget = AnalysisBudget()
    graph = normalize_beans(observation(beans, "beans"), budget=budget)
    report = normalize_conditions(observation(payload), budget=budget)
    catalog = load_source_catalog(catalog_payload, target_id="local", budget=budget)
    result = analyze_missing_beans(
        InspectBeansQuery(
            type_pattern="example.PaymentGateway", context_id="application"
        ),
        graph,
        report,
        catalog,
        budget=budget,
    )
    assert result.state == expected, result.gaps


def test_duplicate_source_candidates_and_wrong_factory_are_not_guessed():
    query, graph, report, catalog, budget = setup_case()
    from dataclasses import replace

    ambiguous = replace(
        catalog,
        producers=(*catalog.producers, replace(catalog.producers[0], id="other")),
    )
    result = analyze_missing_beans(query, graph, report, ambiguous, budget=budget)
    assert result.state == "partial"
    assert "SOURCE_PRODUCER_AMBIGUOUS" in result.gaps
    wrong = replace(
        catalog,
        producers=(
            catalog.producers[0],
            replace(
                catalog.producers[1],
                condition_sources=("example.OtherConfiguration#stripeClient",),
            ),
        ),
    )
    result = analyze_missing_beans(query, graph, report, wrong, budget=AnalysisBudget())
    assert result.state == "partial"
    assert "SOURCE_CONDITIONS_UNOBSERVED" in result.gaps


def test_transitive_condition_cycle_and_runtime_conflict():
    query, graph, report, catalog, budget = setup_case()
    from dataclasses import replace

    nodes = dict(report.nodes)
    client = next(
        node for node in nodes.values() if node.reason_code == "PROPERTY_NOT_FOUND"
    )
    nodes[client.id] = replace(
        client,
        condition_type="OnBeanCondition",
        required_types=("example.PaymentGateway",),
        reason_code="REQUIRED_BEAN_MISSING",
        search="ALL",
    )
    result = analyze_missing_beans(
        query, graph, replace(report, nodes=nodes), catalog, budget=budget
    )
    assert "CONDITION_CYCLE" in result.gaps
    assert result.state != "explained"
    beans = fixture("parent-child/beans.json")
    beans["contexts"]["application"]["beans"]["stripeClient"] = {
        "type": "example.StripeClient",
        "aliases": [],
        "scope": "singleton",
        "dependencies": [],
    }
    conflict_graph = normalize_beans(
        observation(beans, "beans"), budget=AnalysisBudget()
    )
    result = analyze_missing_beans(
        query, conflict_graph, report, catalog, budget=AnalysisBudget()
    )
    assert "CONDITION_RUNTIME_CONFLICT" in result.gaps
    assert result.state != "explained"


def test_unrecognized_grammar_and_large_counts_do_not_escape():
    payload = fixture("stripe-missing/conditions.json")
    row = payload["contexts"]["application"]["negativeMatches"][
        "example.StripePaymentGateway"
    ]["notMatched"][0]
    row["message"] = "AnyNestedCondition " + "9" * 5000 + " matched 0 did not"
    report = normalize_conditions(observation(payload), budget=AnalysisBudget())
    assert report.coverage == "partial"
    assert "9" * 50 not in repr(report)


def test_partial_report_without_explicit_gap_still_cannot_explain():
    from dataclasses import replace

    query, graph, report, catalog, budget = setup_case()
    report = replace(report, coverage="partial", gaps=())
    result = analyze_missing_beans(query, graph, report, catalog, budget=budget)
    assert result.state == "partial"
    assert "CONDITION_REPORT_INCOMPLETE" in result.gaps


@pytest.mark.parametrize(
    "mutation",
    ["missingCondition", "multipleTypes", "propertyMismatchName", "missingBranchRow"],
)
def test_ambiguous_or_malformed_evidence_never_reports_explained(mutation):
    payload = fixture("stripe-missing/conditions.json")
    negative = payload["contexts"]["application"]["negativeMatches"]
    gateway = negative["example.StripePaymentGateway"]["notMatched"][0]
    property_row = negative["example.StripeConfiguration#stripeClient"]["notMatched"][0]
    if mutation == "missingCondition":
        property_row.pop("condition")
    elif mutation == "multipleTypes":
        gateway["message"] = gateway["message"].replace(
            "types: example.StripeClient;",
            "types: example.StripeClient, example.OtherClient;",
        )
    elif mutation == "propertyMismatchName":
        property_row["message"] = (
            "@ConditionalOnProperty (stripe.enabled=true) did not find property 'another'"
        )
    else:
        negative["example.UnrelatedConfiguration"] = {"matched": [], "notMatched": [42]}
    query, graph, _, catalog, budget = setup_case()
    report = normalize_conditions(observation(payload), budget=budget)
    result = analyze_missing_beans(query, graph, report, catalog, budget=budget)
    assert result.state != "explained"
    if mutation == "multipleTypes":
        assert "MULTI_BEAN_REQUIREMENTS_UNSUPPORTED" in result.gaps


def test_source_producer_package_boundary_and_globs_only_star_question():
    from dataclasses import replace

    from spring_runtime_mcp.analyzers.condition_failures import producer_matches

    _, _, _, catalog, _ = setup_case()
    producer = catalog.producers[0]
    assert producer_matches(
        producer,
        InspectBeansQuery(package_prefix="example"),
        catalog,
        budget=AnalysisBudget(),
    )
    assert producer_matches(
        producer,
        InspectBeansQuery(package_prefix="example."),
        catalog,
        budget=AnalysisBudget(),
    )
    assert not producer_matches(
        producer,
        InspectBeansQuery(package_prefix="exam"),
        catalog,
        budget=AnalysisBudget(),
    )
    payment = replace(
        producer, declared_type="example.paymentfake.Gateway", implementation_type=None
    )
    assert not producer_matches(
        payment,
        InspectBeansQuery(package_prefix="example.pay"),
        catalog,
        budget=AnalysisBudget(),
    )
    assert not producer_matches(
        producer,
        InspectBeansQuery(bean_name_pattern="[s]tripePaymentGateway"),
        catalog,
        budget=AnalysisBudget(),
    )
    assert not producer_matches(
        producer,
        InspectBeansQuery(type_pattern="example.[P]aymentGateway"),
        catalog,
        budget=AnalysisBudget(),
    )
    assert producer_matches(
        producer,
        InspectBeansQuery(bean_name_pattern="?tripe*"),
        catalog,
        budget=AnalysisBudget(),
    )
