"""Bounded, no-symlink replay file reads; not an arbitrary ingestion tool."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ..models.common import NormalizationError


def read_replay_file(
    root: Path, path: Path, *, max_bytes: int = 8 * 1024 * 1024
) -> bytes:
    descriptors: list[int] = []
    try:
        relative = path.relative_to(root)
        parts = relative.parts
        if (
            not parts
            or any(p in ("", ".", "..") for p in parts)
            or not hasattr(os, "O_NOFOLLOW")
        ):
            raise NormalizationError("FIXTURE_PATH_DENIED")
        if type(max_bytes) is not int or not 0 < max_bytes <= 8 * 1024 * 1024:
            raise NormalizationError("FIXTURE_PATH_DENIED")
        absolute_root = root.absolute()
        descriptors.append(
            os.open(absolute_root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        )
        for part in absolute_root.parts[1:]:
            if part in (".", ".."):
                raise NormalizationError("FIXTURE_PATH_DENIED")
            descriptors.append(
                os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptors[-1],
                )
            )
        for part in parts[:-1]:
            descriptors.append(
                os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptors[-1],
                )
            )
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=descriptors[-1],
        )
        descriptors.append(descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NormalizationError("FIXTURE_PATH_DENIED")
        result = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - len(result)))
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > max_bytes:
                raise NormalizationError("RESPONSE_TOO_LARGE")
    except NormalizationError:
        raise
    except (OSError, ValueError):
        raise NormalizationError("FIXTURE_PATH_DENIED") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
