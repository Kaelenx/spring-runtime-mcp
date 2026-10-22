"""Application services shared by all tools, with one target read coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

from .models.common import NormalizationError, ParsedObservation, remaining_seconds
from .models.source_catalog import SourceCatalog
from .policy.configuration import ConfigReadPolicy
from .policy.sanitization import SanitizerPipeline
from .snapshots import SnapshotStore


class RuntimeReader(Protocol):
    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation: ...


@dataclass
class Services:
    reader: RuntimeReader
    catalog: SourceCatalog
    sanitizer: SanitizerPipeline
    snapshots: SnapshotStore
    targets: frozenset[str] = frozenset({"local"})
    config_policy: ConfigReadPolicy | None = None
    _read_slots: dict[str, asyncio.Semaphore] = field(default_factory=dict, init=False)

    async def read(
        self, target_id: str, endpoint_id: str, *, snapshot_id: str
    ) -> ParsedObservation:
        if target_id not in self.targets or self.catalog.target_id != target_id:
            raise NormalizationError("TARGET_NOT_REGISTERED")
        slot = self._read_slots.setdefault(target_id, asyncio.Semaphore(2))
        # The outer tool deadline also includes waiting for this shared slot.
        async with asyncio.timeout(remaining_seconds()):
            async with slot:
                return await asyncio.wait_for(
                    self.reader.read(target_id, endpoint_id, snapshot_id=snapshot_id),
                    timeout=remaining_seconds(5.0),
                )
