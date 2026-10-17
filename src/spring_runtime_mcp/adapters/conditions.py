"""Bounded, conservative adapters for known Actuator condition grammars."""

import re
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Literal, cast

from ..models.bean_graph import ContextKey
from ..models.common import AnalysisBudget, Fact, NormalizationError, ParsedObservation
from ..models.conditions import Condition, ConditionReport, Outcome, Search

_IDENT = r"[A-Za-z_$][A-Za-z0-9_.$]*"
_SOURCE = re.compile(_IDENT + r"(?:#[A-Za-z_$][A-Za-z0-9_$]*)?\Z")
_NESTED = re.compile(
    r"(AnyNestedCondition|AllNestedConditions|NoneNestedConditions) (\d{1,4}) matched (\d{1,4}) did not\Z"
)
_BEAN = re.compile(
    r"@ConditionalOn(OnMissingBean|Bean)"
)  # dispatch uses explicit spelling below


def combine(operator: str, values: tuple[Outcome, ...], complete: bool) -> Outcome:
    if operator == "any" and "matched" in values:
        return "matched"
    if (
        operator in ("all", "none")
        and ("notMatched" if operator == "all" else "matched") in values
    ):
        return "notMatched"
    if not complete:
        return "unknown"
    if operator == "any":
        return "notMatched" if all(v == "notMatched" for v in values) else "unknown"
    if operator == "all":
        return "matched" if all(v == "matched" for v in values) else "unknown"
    if operator == "none":
        return "matched" if all(v == "notMatched" for v in values) else "unknown"
    return "unknown"


def _leaf(
    message: str,
) -> tuple[Search, tuple[str, ...], tuple[str, ...], Fact[str], str, bool, Outcome]:
    unknown: Fact[str] = Fact("notReported", None, "NOT_REPORTED")
    bean = re.fullmatch(
        r"@ConditionalOn(Bean|MissingBean) \((types|names): ([A-Za-z0-9_.$, ]+); SearchStrategy: (all|current|parents)\) (.+)",
        message,
    )
    if bean:
        kind, selector, entries, strategy, suffix = bean.groups()
        required = tuple(s.strip() for s in entries.split(","))
        if not required or any(not _SOURCE.fullmatch(s) for s in required):
            return (
                "unknown",
                (),
                (),
                unknown,
                "UNKNOWN_CONDITION_GRAMMAR",
                False,
                "unknown",
            )
        missing = re.fullmatch(
            r"did not find any beans(?: of type " + _IDENT + ")?", suffix
        )
        found = re.fullmatch(r"found bean '[A-Za-z0-9_.$]+'", suffix)
        if missing or found:
            outcome: Outcome = (
                "matched"
                if (bool(missing) == (kind == "MissingBean"))
                else "notMatched"
            )
            return (
                cast(Search, strategy.upper()),
                required if selector == "types" else (),
                required if selector == "names" else (),
                unknown,
                "REQUIRED_BEAN_MISSING"
                if missing and kind == "Bean"
                else "CONDITION_MATCH"
                if outcome == "matched"
                else "BEAN_PRESENT",
                True,
                outcome,
            )
    prop = re.fullmatch(
        r"@ConditionalOnProperty \(([A-Za-z0-9_.-]+)=([A-Za-z0-9_.-]+)\) did not find property '([A-Za-z0-9_.-]+)'",
        message,
    )
    if prop and (prop[1] == prop[3] or prop[1].endswith("." + prop[3])):
        return (
            "unknown",
            (),
            (),
            Fact("known", prop[1], None),
            "PROPERTY_NOT_FOUND",
            True,
            "notMatched",
        )
    mismatch = re.fullmatch(
        r'@ConditionalOnProperty \(([A-Za-z0-9_.-]+)=([A-Za-z0-9_.-]+)\) found different value in property [\'"]([A-Za-z0-9_.-]+)[\'"]',
        message,
    )
    if mismatch and (
        mismatch[1] == mismatch[3] or mismatch[1].endswith("." + mismatch[3])
    ):
        return (
            "unknown",
            (),
            (),
            Fact("known", mismatch[1], None),
            "PROPERTY_VALUE_MISMATCH",
            True,
            "notMatched",
        )
    return "unknown", (), (), unknown, "UNKNOWN_CONDITION_GRAMMAR", False, "unknown"


def normalize_conditions(
    observation: ParsedObservation, *, budget: AnalysisBudget
) -> ConditionReport:
    body = observation.body
    if not isinstance(body, dict) or not isinstance(body.get("contexts"), dict):
        raise NormalizationError("INVALID_CONDITIONS_SHAPE")
    nodes: dict[str, Condition] = {}
    roots: dict[tuple[ContextKey, str], tuple[str, ...]] = {}
    gaps: set[str] = set()

    def parse(
        context: ContextKey,
        source: str,
        condition_type: Any,
        message: Any,
        reported: Outcome,
        depth: int = 0,
    ) -> str:
        budget.consume()
        node_id = f"condition_{len(nodes)}"
        base = Condition(
            node_id,
            context,
            source,
            condition_type
            if isinstance(condition_type, str)
            and len(condition_type) <= 256
            and _SOURCE.fullmatch(condition_type)
            else None,
            "leaf",
            reported,
            "unknown",
            "unknown",
            (),
            (),
            Fact("notReported", None, "NOT_REPORTED"),
            (),
            False,
            "UNKNOWN_CONDITION_GRAMMAR",
            (observation.observation_id,),
        )
        nodes[node_id] = base
        if not isinstance(message, str) or len(message.encode()) > 8192 or depth >= 16:
            gaps.add("UNKNOWN_CONDITION_GRAMMAR")
            return node_id
        segments = re.split(r"; NestedCondition on ", message)
        nested = _NESTED.fullmatch(segments[0])
        if nested:
            operator = {
                "AnyNestedCondition": "any",
                "AllNestedConditions": "all",
                "NoneNestedConditions": "none",
            }[nested[1]]
            entries: list[tuple[str, str]] = []
            valid = True
            for segment in segments[1:]:
                name, sep, text = segment.partition(" ")
                if not sep or not _SOURCE.fullmatch(name):
                    valid = False
                    continue
                entries.append((name, text))
            # A qualified nested class is a parent only if explicitly present.
            if len({name for name, _ in entries}) != len(entries):
                valid = False
            direct = [
                (n, t)
                for n, t in entries
                if not any(n.startswith(p + ".") for p, _ in entries if p != n)
            ]
            children = []
            for name, text in direct:
                descendants = [(n, t) for n, t in entries if n.startswith(name + ".")]
                child_message = text + "".join(
                    "; NestedCondition on " + n + " " + t for n, t in descendants
                )
                child_id = parse(
                    context, source, name, child_message, "unknown", depth + 1
                )
                child = nodes[child_id]
                nodes[child_id] = replace(child, reported_outcome=child.derived_outcome)
                children.append(child_id)
            complete = (
                valid
                and len(children) == int(nested[2]) + int(nested[3])
                and all(nodes[c].children_complete for c in children)
            )
            values = tuple(nodes[c].derived_outcome for c in children)
            counts_match = values.count("matched") == int(nested[2]) and values.count(
                "notMatched"
            ) == int(nested[3])
            complete = complete and counts_match
            derived = combine(operator, values, complete)
            reason = "NESTED_CONDITION" if complete else "UNKNOWN_CONDITION_GRAMMAR"
            nodes[node_id] = replace(
                base,
                operator=cast(Literal["any", "all", "none"], operator),
                derived_outcome=derived,
                child_ids=tuple(children),
                children_complete=complete,
                reason_code=reason,
            )
        else:
            search, types, names, prop, reason, complete, derived = _leaf(message)
            # Annotation text alone cannot establish which Condition implementation ran.
            expected = (
                "OnBeanCondition"
                if types or names
                else "OnPropertyCondition"
                if prop.state == "known"
                else None
            )
            if (
                expected
                and depth == 0
                and (
                    not isinstance(condition_type, str)
                    or len(condition_type) > 256
                    or not _SOURCE.fullmatch(condition_type)
                    or condition_type.rsplit(".", 1)[-1] != expected
                )
            ):
                complete, derived, reason = (
                    False,
                    "unknown",
                    "UNKNOWN_CONDITION_GRAMMAR",
                )
            nodes[node_id] = replace(
                base,
                derived_outcome=derived,
                search=search,
                required_types=types,
                required_names=names,
                property_key=prop,
                reason_code=reason,
                children_complete=complete,
            )
        node = nodes[node_id]
        if (
            reported != "unknown"
            and node.derived_outcome != "unknown"
            and reported != node.derived_outcome
        ):
            nodes[node_id] = replace(
                node, children_complete=False, reason_code="CONDITION_PARSE_CONFLICT"
            )
            gaps.add("CONDITION_PARSE_CONFLICT")
        elif not node.children_complete:
            gaps.add("UNKNOWN_CONDITION_GRAMMAR")
        return node_id

    for context_id, value in sorted(
        body["contexts"].items(), key=lambda item: str(item[0])
    ):
        budget.consume()
        if (
            not isinstance(context_id, str)
            or len(context_id) > 256
            or not isinstance(value, dict)
        ):
            gaps.add("INVALID_CONDITIONS_CONTEXT")
            continue
        context = ContextKey(observation.target_id, observation.snapshot_id, context_id)
        for branch in ("positiveMatches", "negativeMatches"):
            entries = value.get(branch)
            if not isinstance(entries, dict):
                gaps.add("CONDITIONS_BRANCH_UNOBSERVED")
                continue
            for source, matches in sorted(
                entries.items(), key=lambda item: str(item[0])
            ):
                if (
                    not isinstance(source, str)
                    or len(source) > 256
                    or not _SOURCE.fullmatch(source)
                ):
                    gaps.add("INVALID_CONDITION_SOURCE")
                    continue
                groups = (
                    [("matched", matches)]
                    if branch == "positiveMatches"
                    else list(matches.items())
                    if isinstance(matches, dict)
                    else []
                )
                if branch == "negativeMatches" and (
                    not isinstance(matches, dict)
                    or set(matches) != {"matched", "notMatched"}
                ):
                    gaps.add("CONDITIONS_BRANCH_UNOBSERVED")
                for outcome, rows in groups:
                    if outcome not in ("matched", "notMatched") or not isinstance(
                        rows, list
                    ):
                        gaps.add("INVALID_CONDITION_ENTRY")
                        continue
                    ids = list(roots.get((context, source), ()))
                    for row in rows:
                        if not isinstance(row, dict):
                            gaps.add("INVALID_CONDITION_ENTRY")
                            continue
                        ids.append(
                            parse(
                                context,
                                source,
                                row.get("condition"),
                                row.get("message"),
                                cast(Outcome, outcome),
                            )
                        )
                    roots[context, source] = tuple(ids)
    return ConditionReport(
        MappingProxyType(nodes),
        MappingProxyType(roots),
        "partial" if gaps else "complete",
        tuple(sorted(gaps)),
    )
