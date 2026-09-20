# SPDX-License-Identifier: GPL-3.0-or-later
"""Refreshing a filter layer without being asked (PS-057).

The same generator `ops.filter_layer_ops` drives from a modal, pulled
from a `bpy.app.timers` tick instead. What it buys is the ordinary case:
paint under a filter layer, stop, and a moment later the filter shows
what you painted. What it must never do is make Blender feel broken, so
it is fenced in four ways.

**Only the composite path.** A layer whose input needs a Cycles bake is
left alone with a message saying so, because a background render would
lock the window for seconds with nothing to cancel it. The auto path
decides that before allocating anything.

**Only a GPU context that is already known good.** `gpu.init()` crashes
rather than raises on some driverless builds (`gpu_passes/core.py`), so
this path asks `gpu_known()` and refuses to be the caller that finds out.

**Debounced and budgeted.** A pass starts only after things have been
quiet, and each tick spends a slice of a frame on the build. The viewport
goes on showing the previous pixels for the whole job; the commit is one
atomic write, so no intermediate state is ever visible.

**Bounded.** A layer that comes back out of date immediately after its
own build stops refreshing itself and says why, rather than rebuilding
forever. `settled` is the signal that the cycle was genuine: a compile
that found the layer fresh.

Undo, redo and a file read call `cancel_all`. The generator holds the
tree and the node it was started with, and those references do not
survive a restore (PS-090) -- so the rule is that no job does either.
A pixel write the timer made after an undo step was pushed is lost on a
Ctrl+Z together with its stamp, which leaves the layer out of date and
schedules another refresh: self-healing, and worth keeping that way.
"""
from __future__ import annotations

import logging
import time

import bpy

from ..compiler.core import ps_trees
from ..gpu_passes.core import gpu_known
from . import layer_build, layer_plan
from .core import Refused

log = logging.getLogger(__name__)

# Quiet time before a pass starts. Long enough that the tail of updates a
# stroke leaves behind does not start one, short enough to feel automatic.
DEBOUNCE = 0.4
# Seconds of build per tick. Shorter than the modal's slice: that one has
# a progress bar saying where the time went, and this one does not.
BUDGET = 0.02
# Builds of one layer started with no compile finding it fresh in
# between. Real editing always produces one, so reaching this means the
# build cannot satisfy the check that asked for it.
BUILD_LIMIT = 3

_deadline = 0.0
_job = None
# Node uuid to auto builds started since a compile last found that layer
# fresh.
_builds: dict[str, int] = {}


class _Job:
    """A build in flight, and which layer it is for."""

    def __init__(self, tree, node, steps):
        self.tree_name = tree.name
        self.node_name = node.name
        self.uuid = node.uuid
        self.steps = steps

    def close(self):
        # Raises GeneratorExit at whichever yield the build reached,
        # which is what gives its textures back.
        self.steps.close()


# ── What the rest of the addon calls ─────────────────────────────────


def notify() -> None:
    """Ask for a refresh pass once things go quiet. Safe from anywhere.

    Called from a compile that found a filter layer out of date, which
    covers a stroke below one as well: the pixel half of
    `filters.freshness` sets the flag, and the next compile reads it.
    """
    global _deadline
    _deadline = time.monotonic() + DEBOUNCE
    if bpy.app.timers.is_registered(_tick):
        return
    try:
        bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
    except Exception:
        # Can fail during addon unregister; nothing to schedule then.
        log.debug("could not schedule a filter refresh", exc_info=True)


def settled(node) -> None:
    """A compile found *node* up to date, so its build cycle was genuine."""
    _builds.pop(node.uuid, None)


def running() -> bool:
    """True while any layer is being refreshed."""
    return _job is not None


def running_on(node) -> bool:
    """True while a job is building *node*, for the panel and its Cancel button."""
    return _job is not None and _job.uuid == node.uuid


def cancel_all() -> None:
    """Abandon whatever is in flight, leaving its layer's pixels untouched.

    Called from `undo_post`, `redo_post` and `load_post`. The build has
    written nothing unless it reached its last unit, so there is no
    half-written image to clean up.
    """
    global _job
    if _job is not None:
        log.debug("cancelled the refresh of %s", _job.node_name)
        _job.close()
        _job = None
    _builds.clear()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


# ── The tick ─────────────────────────────────────────────────────────


def _tick():
    global _job
    if _job is None:
        remaining = _deadline - time.monotonic()
        if remaining > 0.0:
            return remaining
        _job = _start()
        if _job is None:
            # Nothing to do. `notify` registers this again when there is.
            return None
    return _run(_job)


def _start():
    """The next layer to refresh, as a job, or None when there is none.

    Resolving allocates nothing, so a layer that cannot be refreshed says
    so here and the pass moves on to the next one.
    """
    if gpu_known() is not True:
        # Never the caller that runs `gpu.init()`: it crashes rather than
        # raises where there is no usable driver, and a timer is the worst
        # place to find that out.
        return None
    for tree, node in _candidates():
        try:
            plan = layer_plan.resolve_input(bpy.context, tree, node)
        except Refused as refusal:
            _give_up(node, str(refusal))
            continue
        if not plan.is_composite:
            _give_up(node, f"Update needed: filtering this needs a Cycles bake, "
                           f"because {plan.reason}")
            continue
        if _builds.get(node.uuid, 0) >= BUILD_LIMIT:
            _give_up(node, "Refreshing this layer did not settle; press Update to try again")
            continue
        _set_error(node, "")
        # Counted at the start rather than at the end: the commit marks
        # the tree, so the compile that clears this again runs inside the
        # build's own last unit.
        _builds[node.uuid] = _builds.get(node.uuid, 0) + 1
        log.debug("refreshing %s: %s", node.name, node.stale_reason)
        return _Job(tree, node, layer_build.steps(bpy.context, tree, node, plan=plan))
    return None


def _candidates():
    """Filter layers asking to be refreshed, the bottom of each stack first.

    Bottom first because a filter layer below an out-of-date one has to
    be rebuilt before it, or the upper layer filters pixels that are
    about to change and goes out of date again the moment they do.
    """
    for tree in ps_trees():
        # The stack walks below are the expensive part, and most trees
        # hold no filter layer at all.
        if not any(getattr(node, 'ps_type', "") == 'FILTER' for node in tree.nodes):
            continue
        for channel in tree.channels:
            for item in reversed(tree.stack(channel.name)):
                node = item.node
                if getattr(node, 'ps_type', "") != 'FILTER':
                    continue
                if node.auto_refresh and not node.lock_layer and node.stale_reason:
                    yield tree, node


def _run(job):
    """Spend a slice of this tick on *job*, and say when to come back."""
    global _job
    # At least one unit per tick, whatever the budget: a tick that does
    # nothing and asks to be called again is a spin, not a pause.
    deadline = time.perf_counter() + BUDGET
    while True:
        try:
            next(job.steps)
        except StopIteration as done:
            _job = None
            log.debug("refreshed %s", done.value.name)
            return DEBOUNCE
        except Refused as refusal:
            _job = None
            _give_up(_node_of(job), str(refusal))
            return DEBOUNCE
        except Exception:
            # A driver that gives up mid-build. Logged and stopped rather
            # than retried: the same build would fail the same way, and
            # the layer still shows the pixels it had.
            _job = None
            job.close()
            log.exception("could not refresh filter layer '%s'", job.node_name)
            _give_up(_node_of(job), "Refreshing this layer failed; see the system console")
            return DEBOUNCE
        if time.perf_counter() >= deadline:
            return 0.0


def _node_of(job):
    """*job*'s node, re-fetched, or None when it did not survive (PS-090)."""
    tree = bpy.data.node_groups.get(job.tree_name)
    if tree is None:
        return None
    node = tree.nodes.get(job.node_name)
    return node if node is not None and getattr(node, 'uuid', "") == job.uuid else None


def _give_up(node, message: str) -> None:
    """Stop refreshing *node* and say why, in the panel rather than a popup."""
    if node is None:
        return
    node.auto_refresh = False
    _set_error(node, message)
    log.debug("auto refresh off for %s: %s", node.name, message)


def _set_error(node, message: str) -> None:
    # Writing an RNA property tags the tree and the materials using it,
    # so only write when the value actually changes.
    if node.derived_error != message:
        node.derived_error = message
