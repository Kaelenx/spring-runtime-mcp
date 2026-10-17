"""Immutable, target-scoped identities and dependency graph facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .common import Fact


@dataclass(frozen=True, order=True)
class ContextKey:
    target_id: str
    snapshot_id: str
    context_id: str


@dataclass(frozen=True, order=True)
class BeanKey:
    context: ContextKey
    canonical_name: str
    bean_kind: Literal["bean", "factory", "product", "unknown"] = "unknown"


@dataclass(frozen=True)
class Context:
    key: ContextKey
    parent: Fact[ContextKey | None]
    complete: bool


@dataclass(frozen=True)
class Bean:
    key: BeanKey
    id: str
    aliases: tuple[str, ...]
    reported_type: Fact[str]
    scope: Fact[str]
    resource: Fact[str]
    requested_dependencies: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    state: Literal["resolved", "ambiguous", "unresolved"]
    candidates: tuple[BeanKey, ...]
    reason_code: str | None


@dataclass(frozen=True)
class DependencyEdge:
    from_key: BeanKey
    requested_name: str
    resolution: Resolution
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class BeanGraph:
    contexts: Mapping[ContextKey, Context]
    beans: Mapping[BeanKey, Bean]
    names: Mapping[ContextKey, Mapping[str, tuple[BeanKey, ...]]]
    canonical_to_names: Mapping[BeanKey, tuple[str, ...]]
    forward: Mapping[BeanKey, tuple[DependencyEdge, ...]]
    reverse: Mapping[BeanKey, tuple[DependencyEdge, ...]]
    coverage: Literal["complete", "partial", "unavailable"]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class GraphSlice:
    roots: tuple[BeanKey, ...]
    edges: tuple[tuple[DependencyEdge, int], ...]
    truncated: bool
    gaps: tuple[str, ...]
