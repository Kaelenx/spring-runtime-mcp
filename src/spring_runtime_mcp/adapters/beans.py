"""Normalize Actuator beans without inventing absent container facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeGuard

from ..models.bean_graph import Bean, BeanGraph, BeanKey, Context, ContextKey
from ..models.common import (
    AnalysisBudget,
    AnalysisLimit,
    Fact,
    NormalizationError,
    ParsedObservation,
)


def _text(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and 0 < len(value) <= 4096


def _fact(record: Mapping[str, Any], name: str) -> Fact[str]:
    value = record.get(name)
    if _text(value):
        return Fact("known", value, None)
    return Fact("notReported", None, "FIELD_NOT_REPORTED")


def normalize_beans(
    observation: ParsedObservation, *, budget: AnalysisBudget
) -> BeanGraph:
    """Build all local name indices before resolving any dependency edges."""
    body = observation.body
    if not isinstance(body, Mapping) or not isinstance(body.get("contexts"), Mapping):
        raise NormalizationError("INVALID_BEANS_STRUCTURE")
    contexts: dict[ContextKey, Context] = {}
    beans: dict[BeanKey, Bean] = {}
    names: dict[ContextKey, dict[str, tuple[BeanKey, ...]]] = {}
    canonical: dict[BeanKey, tuple[str, ...]] = {}
    gaps: set[str] = set()
    try:
        for context_id, raw_context in sorted(
            body["contexts"].items(), key=lambda item: str(item[0])
        ):
            budget.consume()
            if not _text(context_id):
                gaps.add("INVALID_CONTEXT_NAME")
                continue
            key = ContextKey(observation.target_id, observation.snapshot_id, context_id)
            names[key] = {}
            if not isinstance(raw_context, Mapping):
                contexts[key] = Context(
                    key, Fact("unknown", None, "INVALID_CONTEXT"), False
                )
                gaps.add("INVALID_CONTEXT")
                continue
            parent_id = raw_context.get("parentId")
            if "parentId" not in raw_context:
                # Boot reports parentId only for child contexts; omitting it
                # means this is a root context, not unobserved hierarchy.
                parent: Fact[ContextKey | None] = Fact("known", None, None)
            elif parent_id is None:
                parent = Fact("known", None, None)
            elif _text(parent_id):
                parent = Fact(
                    "known", ContextKey(key.target_id, key.snapshot_id, parent_id), None
                )
            else:
                parent = Fact("unknown", None, "INVALID_PARENT_CONTEXT")
                gaps.add("INVALID_PARENT_CONTEXT")
            raw_beans = raw_context.get("beans")
            contexts[key] = Context(key, parent, isinstance(raw_beans, Mapping))
            if not isinstance(raw_beans, Mapping):
                gaps.add("BEAN_INDEX_INCOMPLETE")
                continue
            for name, raw in sorted(raw_beans.items(), key=lambda item: str(item[0])):
                budget.consume()
                if not _text(name) or not isinstance(raw, Mapping):
                    contexts[key] = Context(key, parent, False)
                    gaps.add("BEAN_INDEX_INCOMPLETE")
                    continue
                bean_key = BeanKey(key, name)
                aliases_raw = raw.get("aliases")
                if not isinstance(aliases_raw, list) or not all(
                    _text(alias) for alias in aliases_raw
                ):
                    aliases = ()
                    contexts[key] = Context(key, parent, False)
                    gaps.add("ALIASES_NOT_REPORTED")
                else:
                    aliases = tuple(sorted(set(aliases_raw)))
                dependencies_raw = raw.get("dependencies")
                if not isinstance(dependencies_raw, list) or not all(
                    _text(dep) for dep in dependencies_raw
                ):
                    dependencies = ()
                    gaps.add("DEPENDENCIES_NOT_REPORTED")
                else:
                    dependencies = tuple(sorted(set(dependencies_raw)))
                # Opaque ordinals never encode or hash untrusted labels.
                identifier = f"bean_{len(beans) + 1:06d}"
                beans[bean_key] = Bean(
                    bean_key,
                    identifier,
                    aliases,
                    _fact(raw, "type"),
                    _fact(raw, "scope"),
                    _fact(raw, "resource"),
                    dependencies,
                    (observation.observation_id,),
                )
                all_names = tuple(sorted({name, *aliases}))
                canonical[bean_key] = all_names
                for local_name in all_names:
                    budget.consume()
                    names[key][local_name] = tuple(
                        sorted((*names[key].get(local_name, ()), bean_key))
                    )
    except AnalysisLimit:
        gaps.add("GRAPH_LIMIT_REACHED")
        # Any interrupted index may have omitted names; no ancestor fallback is safe.
        contexts = {
            key: Context(key, ctx.parent, False) for key, ctx in contexts.items()
        }
    for context in contexts.values():
        if (
            context.parent.state == "known"
            and context.parent.value is not None
            and context.parent.value not in contexts
        ):
            gaps.add("PARENT_CONTEXT_UNOBSERVED")
    checked: set[ContextKey] = set()
    try:
        for start in contexts:
            current: ContextKey | None = start
            path: set[ContextKey] = set()
            while current is not None and current not in checked:
                budget.consume()
                if current in path:
                    gaps.add("CONTEXT_CYCLE")
                    break
                path.add(current)
                ancestor = contexts.get(current)
                if ancestor is None or ancestor.parent.state != "known":
                    break
                current = ancestor.parent.value
            checked.update(path)
    except AnalysisLimit:
        gaps.add("GRAPH_LIMIT_REACHED")
    return BeanGraph(
        contexts,
        beans,
        names,
        canonical,
        {},
        {},
        "partial" if gaps else "complete",
        tuple(sorted(gaps)),
    )
