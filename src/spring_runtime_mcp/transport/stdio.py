"""Quarantine Python and native library console writes while computing a frame."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator


@contextlib.contextmanager
def quiet_console() -> Iterator[None]:
    """Discard diagnostic-library output, including os.write(1/2), not log it.

    This process-wide boundary is for the single-process replay CLI. A future
    concurrent SDK host needs an equivalent lifetime boundary at its entrypoint.
    """
    saved_out, saved_err = os.dup(1), os.dup(2)
    original_streams = (sys.stdout, sys.stderr)
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
    finally:
        # Libraries can retain an earlier TextIOWrapper. Flush those buffers
        # while the descriptors still point at the discard sink.
        for stream in original_streams:
            try:
                stream.flush()
            except (OSError, ValueError):
                continue
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)


def seal_console() -> None:
    """Keep CLI shutdown/atexit library writes away from the protocol channel."""
    with open(os.devnull, "w", encoding="utf-8") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
