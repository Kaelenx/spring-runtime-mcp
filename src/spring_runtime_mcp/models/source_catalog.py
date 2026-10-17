"""Explicit, target-bound workspace facts; never inferred from Bean names."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TypeFact:
    name: str
    direct_supertypes: tuple[str, ...]


@dataclass(frozen=True)
class SourceProducer:
    id: str
    context_id: str
    bean_name: str
    aliases: tuple[str, ...]
    declared_type: str
    implementation_type: str | None
    configuration_class: str
    factory_method: str | None
    condition_sources: tuple[str, ...]
    source_file: str
    source_line: int
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceCatalog:
    target_id: str
    source_revision: str | None
    types: Mapping[str, TypeFact]
    producers: tuple[SourceProducer, ...]
    gaps: tuple[str, ...]
