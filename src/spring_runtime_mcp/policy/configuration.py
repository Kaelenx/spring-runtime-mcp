"""Immutable operator registration for config reads, never tool arguments."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigReadPolicy:
    properties: frozenset[str] = frozenset()
    prefixes: frozenset[str] = frozenset()
    allow_values: bool = False
    profiles: bool = False

    def __post_init__(self) -> None:
        for name in ("properties", "prefixes"):
            values = frozenset(getattr(self, name))
            if len(values) > 100 or any(
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,255}", value) is None
                for value in values
            ):
                raise ValueError("INVALID_CONFIG_REGISTRATION")
            object.__setattr__(self, name, values)
        if type(self.allow_values) is not bool or type(self.profiles) is not bool:
            raise ValueError("INVALID_CONFIG_REGISTRATION")

    def allows(self, key: str) -> bool:
        return key in self.properties or any(
            key == prefix or key.startswith(prefix + ".") for prefix in self.prefixes
        )
