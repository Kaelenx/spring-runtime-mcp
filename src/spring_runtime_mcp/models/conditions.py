"""Condition facts exclude the untrusted diagnostic message."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .bean_graph import ContextKey
from .common import Fact

Outcome = Literal["matched", "notMatched", "unknown"]
Search = Literal["CURRENT", "PARENTS", "ALL", "unknown"]


@dataclass(frozen=True)
class Condition:
    id: str
    context: ContextKey
    source: str
    condition_type: str | None
    operator: Literal["leaf", "any", "all", "none", "unknown"]
    reported_outcome: Outcome
    derived_outcome: Outcome
    search: Search
    required_types: tuple[str, ...]
    required_names: tuple[str, ...]
    property_key: Fact[str]
    child_ids: tuple[str, ...]
    children_complete: bool
    reason_code: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConditionReport:
    nodes: Mapping[str, Condition]
    roots_by_source: Mapping[tuple[ContextKey, str], tuple[str, ...]]
    coverage: Literal["complete", "partial", "unavailable"]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class BlockingEdge:
    from_id: str
    to_id: str
    reason_code: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class FailureAnalysis:
    state: Literal["notNeeded", "explained", "partial", "unknown"]
    conditions: tuple[Condition, ...]
    blocking_edges: tuple[BlockingEdge, ...]
    blocking_paths: tuple[tuple[str, ...], ...]
    truncated: bool
    gaps: tuple[str, ...]
