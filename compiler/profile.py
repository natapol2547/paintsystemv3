"""Per-phase compile timings, switched on with ``PS_PROFILE``.

A compile runs on every edit, so the profiler has to cost nothing when it is
off. ``PS_PROFILE`` is read once, at import: with it unset, ``phase`` returns
one shared do-nothing context manager instead of building a timer, so a
measured phase costs a call and a branch and allocates nothing.

With it set, every phase logs how long it took and which tree it worked on::

    PS_PROFILE=1 blender -b --factory-startup --python tests/test_compile.py

    [ps profile] normalize      0.01 ms  Stack
    [ps profile] build_ir       2.54 ms  Stack
    [ps profile] fingerprint    0.08 ms  Stack
    [ps profile] upsert         0.20 ms  PS Stack [5aa4b105]
    [ps profile] links          0.18 ms  PS Stack [5aa4b105]
    [ps profile] arrange        0.19 ms  PS Stack [5aa4b105]
    [ps profile] apply          0.71 ms  Stack BuildStats(nodes_created=1, ...)

The phases nest: ``upsert``, ``links`` and ``arrange`` are the inside of
``apply``, on the artifact rather than on the tree, and a build of a library
group during ``build_ir`` logs its own three. Phases sit outside every
per-node loop, so their count per compile is fixed and the timings do not
measure the profiler itself.
"""
from __future__ import annotations

import logging
import os
from time import perf_counter
from typing import Any

log = logging.getLogger(__name__)

_enabled = os.environ.get("PS_PROFILE", "") not in ("", "0")


class _NoProfile:
    """What ``phase`` returns while profiling is off.

    One instance serves every call site: it holds no state, so nothing has to
    be allocated or reset between phases.
    """

    __slots__ = ()

    def __enter__(self) -> _NoProfile:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def detail(self, value: Any) -> None:
        pass


class _Phase:
    """Times one phase and logs it on the way out, exception or not."""

    __slots__ = ("_name", "_tree", "_detail", "_start")

    def __init__(self, name: str, tree: Any) -> None:
        self._name = name
        self._tree = tree
        self._detail: Any = None
        self._start = 0.0

    def __enter__(self) -> _Phase:
        self._start = perf_counter()
        return self

    def __exit__(self, *exc: Any) -> bool:
        elapsed_ms = (perf_counter() - self._start) * 1000
        name = getattr(self._tree, "name", "")
        detail = "" if self._detail is None else f" {self._detail}"
        # Phase and duration first: artifact names are long enough to push
        # the numbers out of line, and the numbers are what gets read.
        log.info("%-11s %7.2f ms  %s%s", self._name, elapsed_ms, name, detail)
        return False

    def detail(self, value: Any) -> None:
        """Log *value* after the timing, to say what the phase did."""
        self._detail = value


_NO_PROFILE = _NoProfile()


def phase(name: str, tree: Any = None) -> _NoProfile | _Phase:
    """Time the block as *name*, working on *tree*.

    *tree* is only read when profiling is on, so the disabled path does not
    touch RNA either.
    """
    if not _enabled:
        return _NO_PROFILE
    return _Phase(name, tree)


def _configure_logging() -> None:
    """Give the timings somewhere to go.

    They are logged at INFO, which Blender drops: nothing configures logging,
    so logging's last-resort handler prints WARNING and above and nothing
    else. Setting ``PS_PROFILE`` is a request for these lines, so this logger
    gets its own handler and stops propagating, which keeps the output at one
    line per phase whatever configures logging afterwards.
    """
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        # Reloading the add-on re-runs this module against the same logger.
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[ps profile] %(message)s"))
        log.addHandler(handler)


if _enabled:
    _configure_logging()
