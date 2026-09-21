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

**Restarted when what it read moves.** A setting dragged or a stroke
finished partway through a build leaves that build filtering the past.
It would still be correct to finish -- `filters.layer_build` stamps what
it read, so the layer comes out still out of date -- but the time spent
is wasted, so the job is dropped and a new one waits out the debounce.
Neither kind of ending counts towards the bound, which is for a build
that cannot satisfy its own check rather than one that was overtaken.
After a few restarts in a row the build is let finish anyway, so that
someone painting in short bursts still sees the filter catch up.

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
from . import freshness, layer_build, layer_plan
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
# Builds of one layer dropped in a row because what they read moved.
# The next one runs to the end whatever happens meanwhile.
RESTART_LIMIT = 2

_deadline = 0.0
_job = None
# Whether `notify` has been called since the running job last looked.
_poked = False
# Node uuid to auto builds started since a compile last found that layer
# fresh.
_builds: dict[str, int] = {}
# Node uuid to builds of it dropped in a row, see RESTART_LIMIT.
_restarts: dict[str, int] = {}


class _Job:
    """A build in flight, which layer it is for, and what it read."""

    def __init__(self, tree, node, plan):
        self.tree_name = tree.name
        self.node_name = node.name
        self.uuid = node.uuid
        # Taken in the same tick as the build's own, which is the first
        # unit `_run` drives, so the two agree.
        self.read = layer_build.inputs_of(tree, node, plan)
        self.steps = layer_build.steps(bpy.context, tree, node, plan=plan)

    def painted_over(self) -> bool:
        """Whether a stroke has landed below the layer since the build started."""
        return freshness.changes(self.uuid) != self.read[1]

    def moved(self) -> bool:
        """Whether anything the build read has changed since it started.

        About a millisecond: one resolve and one IR build, no pixels.
        """
        node = _node_of(self)
        if node is None:
            return True
        tree = node.id_data
        try:
            plan = layer_plan.resolve_input(bpy.context, tree, node)
        except Refused:
            return True
        return layer_build.inputs_of(tree, node, plan) != self.read

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
    It is also what tells a running job to check whether it has been
    overtaken.
    """
    global _deadline, _poked
    _deadline = time.monotonic() + DEBOUNCE
    _poked = True
    if bpy.app.timers.is_registered(_tick):
        return
    try:
        bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
    except Exception:
        # Can fail during addon unregister; nothing to schedule then.
        log.debug("could not schedule a filter refresh", exc_info=True)


def settled(node) -> None:
    """A compile found *node* up to date, so its build cycle was genuine.

    Also how a job learns that its layer went back to what it was built
    from partway through -- a setting nudged and returned -- which leaves
    nothing to build, so no `notify` to hear it by.
    """
    global _poked
    _builds.pop(node.uuid, None)
    if running_on(node):
        _poked = True


def running() -> bool:
    """True while any layer is being refreshed."""
    return _job is not None


def running_on(node) -> bool:
    """True while a job is building *node*, for the panel and its Cancel button."""
    return _job is not None and _job.uuid == node.uuid


def cancel_all() -> None:
    """Abandon whatever is in flight, leaving its layer's pixels untouched.

    Called before an undo, a redo or a file load, when the Update button
    starts its own build, from the Cancel button, and on unregister. The
    build writes nothing before its last unit, so there is no
    half-written image to clean up.
    """
    global _job
    if _job is not None:
        log.debug("cancelled the refresh of %s", _job.node_name)
        _job.close()
        _job = None
    _builds.clear()
    _restarts.clear()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


# ── The tick ─────────────────────────────────────────────────────────


def _tick():
    global _job, _poked
    if _job is not None and _overtaken(_job):
        _restart(_job)
    if _job is None:
        remaining = _deadline - time.monotonic()
        if remaining > 0.0:
            return remaining
        _job = _start()
        if _job is None:
            # Nothing to do. `notify` registers this again when there is.
            return None
        _poked = False
    return _run(_job)


def _overtaken(job) -> bool:
    """Whether *job* is filtering pixels or settings that have since moved.

    A stroke below is a count to compare, so it is looked for on every
    tick: the addon's own pixel writes mark a layer that is already
    marked without anything calling `notify`. A structural change costs
    an IR build to see, and always comes through a compile that does.
    """
    global _poked
    poked, _poked = _poked, False
    if _restarts.get(job.uuid, 0) >= RESTART_LIMIT:
        return False
    if job.painted_over():
        return True
    if not poked:
        return False
    moved = job.moved()
    # The IR build inside `moved` compiles the layer, which is still
    # stale, so it calls `notify` again. That call is not news, and
    # left set it would make every tick pay for another IR build.
    _poked = False
    return moved


def _restart(job) -> None:
    """Drop *job*, which is filtering pixels or settings that have moved on.

    The next pass starts it again once things have been quiet for the
    debounce, counted from now: a stroke found by counting came with no
    `notify` to push the deadline out.
    """
    global _job, _deadline
    _job = None
    _deadline = max(_deadline, time.monotonic() + DEBOUNCE)
    job.close()
    _uncount(job)
    _restarts[job.uuid] = _restarts.get(job.uuid, 0) + 1
    log.debug("restarting the refresh of %s: what it read has moved", job.node_name)


def _uncount(job) -> None:
    """Take *job* back off `_builds`: it was overtaken, not unsettled."""
    count = _builds.get(job.uuid, 0) - 1
    if count > 0:
        _builds[job.uuid] = count
    else:
        _builds.pop(job.uuid, None)


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
        return _Job(tree, node, plan)
    return None


def _candidates():
    """Filter layers asking to be refreshed, the bottom of each stack first.

    Bottom first because a filter layer below an out-of-date one has to
    be rebuilt before it, or the upper layer filters pixels that are
    about to change and goes out of date again the moment they do.

    A switched-off layer is left alone. It renders as a pass-through, so
    a rebuild would spend the video memory and the frame time of a full
    composite on pixels nothing can show, and turning it off is the
    ordinary way to compare with and without. It stays marked out of
    date, and switching it back on is what asks for the refresh -- see
    `PaintSystemFilterLayerNode._enabled_changed`, which cannot leave
    that to the compile it schedules.

    A layer the Update button is already building is skipped too. Its
    build's first unit compiles, which calls `notify`, so a long Update
    would otherwise start a second build of the same layer. The commit
    compiles again, and that asks for a refresh if one is still needed.
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
                if (node.enabled and node.auto_refresh and not node.lock_layer
                        and node.stale_reason and not freshness.building(node.uuid)):
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
            _restarts.pop(job.uuid, None)
            # Let finish past RESTART_LIMIT, or overtaken in its last
            # unit. It committed what it read and the layer is still out
            # of date, which says nothing about whether it can settle.
            if job.moved():
                _uncount(job)
            log.debug("refreshed %s", done.value.name)
            return DEBOUNCE
        except Refused as refusal:
            _job = None
            _restarts.pop(job.uuid, None)
            _give_up(_node_of(job), str(refusal))
            return DEBOUNCE
        except Exception:
            # A driver that gives up mid-build. Logged and stopped rather
            # than retried: the same build would fail the same way, and
            # the layer still shows the pixels it had.
            _job = None
            _restarts.pop(job.uuid, None)
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
