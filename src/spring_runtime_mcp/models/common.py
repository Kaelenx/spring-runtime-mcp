"""Shared observation, uncertainty and request budget models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")


class NormalizationError(ValueError):
    """An expected, safe diagnostic failure; never attach upstream text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AnalysisLimit(NormalizationError):
    """The shared request analysis budget has been exhausted."""

    def __init__(self, code: str = "GRAPH_LIMIT_REACHED") -> None:
        super().__init__(code)


@dataclass(frozen=True)
class Fact(Generic[T]):
    state: Literal["known", "unknown", "notReported", "redacted"]
    value: T | None
    reason_code: str | None = None


@dataclass
class AnalysisBudget:
    max_visits: int = 20_000
    max_depth: int = 16
    deadline_monotonic: float | None = None
    monotonic: Callable[[], float] | None = None
    consumed_visits: int = 0

    def consume(self, visits: int = 1) -> None:
        if visits < 0:
            raise ValueError("INVALID_BUDGET_INCREMENT")
        self.consumed_visits += visits
        if self.consumed_visits > self.max_visits:
            raise AnalysisLimit()
        if (
            self.deadline_monotonic is not None
            and self.monotonic is not None
            and self.monotonic() >= self.deadline_monotonic
        ):
            raise AnalysisLimit("TARGET_TIMEOUT")


@dataclass(frozen=True)
class ParsedObservation:
    target_id: str
    snapshot_id: str
    observation_id: str
    observed_at: str
    endpoint_id: str
    body: Mapping[str, Any] | None
    http_status: int | None = 200
    error_code: str | None = None
    representation: str = "actuatorV3"
