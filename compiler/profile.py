"""Per-phase compile timings, turned on with the ``PS_PROFILE`` variable.

Entry point: ``phase(name, tree)``, used as a context manager.

A compile runs on every edit, so the profiler must cost nothing when it is
off. ``PS_PROFILE`` is read once, at import. When it is unset, ``phase``
returns one shared do-nothing context manager instead of a timer. A
measured phase then costs one call and one branch, and allocates nothing.

When it is set, every phase logs its duration and the tree it worked on::

    PS_PROFILE=1 blender -b --factory-startup --python tests/test_compile.py

    [ps profile] normalize      0.01 ms  Stack
    [ps profile] build_ir       2.54 ms  Stack
    [ps profile] fingerprint    0.08 ms  Stack
    [ps profile] upsert         0.20 ms  PS Stack [5aa4b105]
    [ps profile] links          0.18 ms  PS Stack [5aa4b105]
    [ps profile] arrange        0.19 ms  PS Stack [5aa4b105]
    [ps profile] apply          0.71 ms  Stack BuildStats(nodes_created=1, ...)

Phases nest. ``upsert``, ``links`` and ``arrange`` run inside ``apply``,
and they name the artifact, not the Paint System tree. A library group
built during ``build_ir`` logs its own three. No phase sits inside a
per-node loop. So each compile logs a fixed number of phases, and the
timings do not measure the profiler itself.
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

    One instance serves every call site. It holds no state, so nothing is
    allocated or reset between phases.
    """

    __slots__ = ()

    def __enter__(self) -> _NoProfile:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def detail(self, value: Any) -> None:
        pass


class _Phase:
    """Time one phase and log it on exit, even when the block raises."""

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
        # Phase name and duration come first. Artifact names are long and
        # would push the numbers out of line, and the numbers are what
        # people read.
        log.info("%-11s %7.2f ms  %s%s", self._name, elapsed_ms, name, detail)
        return False

    def detail(self, value: Any) -> None:
        """Log *value* after the timing, to say what the phase did."""
        self._detail = value


_NO_PROFILE = _NoProfile()


def phase(name: str, tree: Any = None) -> _NoProfile | _Phase:
    """Time the block as *name*, working on *tree*.

    *tree* is only read when profiling is on. So the disabled path does not
    read Blender properties (RNA) either.
    """
    if not _enabled:
        return _NO_PROFILE
    return _Phase(name, tree)


def _configure_logging() -> None:
    """Give this logger its own handler, so the timings are printed.

    The timings are logged at INFO. Nothing in Blender configures logging,
    so Python's last-resort handler prints only WARNING and above, and INFO
    lines are lost. Setting ``PS_PROFILE`` asks for these lines, so this
    logger gets its own handler. It also stops passing records to parent
    loggers. That keeps the output at one line per phase, even if something
    configures logging later.
    """
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        # An add-on reload runs this module again on the same logger, so
        # only add the handler once.
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[ps profile] %(message)s"))
        log.addHandler(handler)


if _enabled:
    _configure_logging()
