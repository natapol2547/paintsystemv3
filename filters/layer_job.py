# SPDX-License-Identifier: GPL-3.0-or-later
"""Builds filter layers automatically when they are new or out of date (PS-057).

Runs the same build generator that `ops.filter_layer_ops` drives from a
modal operator, but from a `bpy.app.timers` tick. The goal: add a filter
layer, or paint under one, stop, and a moment later the filter shows the
result. It must never make Blender feel broken, so it has these limits.

- **Composite path only.** A layer whose input needs a Cycles bake is
  left alone, with a message saying so. A background render would lock
  the window for seconds with no way to cancel it. This is decided
  before anything is allocated.
- **Refusals wait.** A layer that cannot be built right now, such as one
  with nothing below it or with no mesh to resolve its UV map on, shows
  the reason and keeps Auto Refresh on. The next change to the tree or
  the scene asks again, so adding a layer below it, or selecting the
  mesh, is enough. Once a build has needed a mesh, the layer stores it
  (`filters.layer_plan.keep_surface`), so later refreshes run whatever is
  selected. A build that has started and then fails, is
  refused, or does not settle switches Auto Refresh off instead. Trying
  it again would repeat the same failure, often after allocating GPU
  memory.
- **Only a GPU context already known to work.** `gpu.init()` crashes
  instead of raising on some builds without a driver
  (`gpu_passes/core.py`). So this path asks `gpu_known()` and is never
  the first caller to find out. It also waits while no context is
  bound, which on 5.3 happens for a moment after a file is read.
- **Debounced and budgeted.** A build starts only after things have
  been quiet for `DEBOUNCE` seconds, and each tick spends about `BUDGET`
  seconds on it. The viewport shows the previous pixels for the whole
  job. The commit is one atomic write, so no half-built state is ever
  visible.
- **One pair at a time.** Each pair of a filter layer has its own result,
  so each is its own job, and the counts below are kept per pair
  (`nodetree.stack_ops.pair_key`). Auto Refresh is the node's, so giving
  up on one pair switches it off for all of them.
- **Bounded.** A pair that is out of date again right after its own
  build stops refreshing itself and says why, instead of rebuilding
  forever. `settled` is the signal that a cycle ended properly: a
  compile found the pair up to date.
- **Restarted when its input changes.** A setting dragged or a stroke
  finished during a build means the build is filtering old input.
  Finishing would still be correct, because `filters.layer_build` stamps
  what it read and the layer stays out of date. But the time would be
  wasted, so the job is dropped and a new one waits for the debounce.
  Neither case counts towards `BUILD_LIMIT`, which is for a build that
  cannot satisfy its own check, not one that was overtaken. After
  `RESTART_LIMIT` restarts in a row the build is allowed to finish, so
  someone painting in short bursts still sees the filter catch up.

Undo, redo and a file read call `cancel_all`. A job holds the tree and
the node it started with, and those references do not survive a restore
(PS-090), so no job may survive one either. For the same reason, a job
whose pair, layer or tree was removed is dropped by the next tick before
it runs another unit (`_drop`). A pixel write the timer made
after an undo step was pushed is lost on Ctrl+Z together with its stamp.
The layer is then out of date and schedules another refresh, so this
heals itself. Keep it that way.
"""
from __future__ import annotations

import logging
import time

import bpy

from ..compiler.core import ps_trees
from ..gpu_passes.core import context_active, gpu_known
from ..nodetree.stack_ops import Position, pair_key
from . import freshness, layer_build, layer_plan
from .core import Refused

log = logging.getLogger(__name__)

# Seconds of quiet before a build starts. Long enough that the updates at
# the end of a stroke do not start one, short enough to feel automatic.
DEBOUNCE = 0.4
# Seconds of build work per tick. Shorter than the modal operator's
# slice, because the modal shows a progress bar and this does not.
BUDGET = 0.02
# Most builds of one pair with no compile finding it up to date in
# between. Normal editing always produces such a compile, so reaching
# this means the build cannot satisfy the check that asked for it.
BUILD_LIMIT = 3
# Most builds of one pair dropped in a row because their input changed.
# The next one runs to the end whatever happens meanwhile.
RESTART_LIMIT = 2

_deadline = 0.0
_job = None
# Whether `notify` has been called since the running job last checked.
_poked = False
# Pair key to auto builds started since a compile last found that pair
# up to date.
_builds: dict[str, int] = {}
# Pair key to builds of it dropped in a row, see RESTART_LIMIT.
_restarts: dict[str, int] = {}
# Whether a pair was refused and is waiting for a change that lets it
# build. Only a pass that looked at every pair may lower it.
_waiting = False


class _Job:
    """A build in flight, which pair it is for, and what it read.

    The pair is held by its output's identifier, which stays with it
    when a pair before it is removed. Its position does not.
    """

    def __init__(self, tree, node, pair, plan):
        self.tree_name = tree.name
        self.node_name = node.name
        self.uuid = node.uuid
        self.output = node.outputs[pair].identifier
        self.key = pair_key(node, pair)
        # Taken in the same tick as the build's own `inputs_of` call,
        # which runs in the first unit `_run` drives, so the two agree.
        self.read = layer_build.inputs_of(tree, node, pair, plan)
        self.steps = layer_build.steps(bpy.context, tree, node, pair, plan=plan)

    def painted_over(self) -> bool:
        """Whether a stroke has landed below the pair since the build started."""
        return freshness.changes(self.key) != self.read[1]

    def moved(self) -> bool:
        """Whether anything the build read has changed since it started.

        Costs about a millisecond: one resolve and one IR build, with no
        pixels read.
        """
        position = _position_of(self)
        if position is None:
            return True
        node, pair = position
        tree = node.id_data
        try:
            plan = layer_plan.resolve_input(bpy.context, tree, node, pair)
        except Refused:
            return True
        return layer_build.inputs_of(tree, node, pair, plan) != self.read

    def close(self):
        # Raises GeneratorExit at the build's current yield, which frees
        # its textures.
        self.steps.close()


# ── What the rest of the addon calls ─────────────────────────────────


def notify() -> None:
    """Ask for a refresh once things go quiet. Safe to call from anywhere.

    Called by a compile that found a filter layer out of date. This also
    covers a stroke below a filter layer, because the pixel half of
    `filters.freshness` sets the flag and the next compile reads it. It
    also tells a running job to check whether it has been overtaken.
    """
    global _deadline, _poked
    _deadline = time.monotonic() + DEBOUNCE
    _poked = True
    if bpy.app.timers.is_registered(_tick):
        return
    try:
        bpy.app.timers.register(_tick, first_interval=DEBOUNCE)
    except Exception:
        # Can fail during addon unregister, when there is nothing to
        # schedule anyway.
        log.debug("could not schedule a filter refresh", exc_info=True)


def settled(node, pair: int) -> None:
    """Called when a compile finds *node*'s *pair* up to date. Resets its build count.

    This is also how a running job learns that its pair went back to
    what the job read, for example a setting nudged and then returned.
    Then nothing is out of date, so no `notify` call would tell the job.

    A refusal message on a pair with Auto Refresh on is cleared here.
    The pair no longer needs a build, so the reason it could not have
    one no longer matters.
    """
    global _poked
    _builds.pop(pair_key(node, pair), None)
    if node.auto_refresh:
        _set_error(node.pairs[pair], "")
    if running_on(node, pair):
        _poked = True


def scene_changed() -> None:
    """Ask again for any layer that is waiting. Called on every depsgraph update.

    Some refusals are about the scene rather than the tree: no mesh that
    uses the tree is selected, or the mesh lacks the UV map a layer names. Selecting the mesh
    or adding the UV map compiles no tree, so no `notify` call would
    come. This costs nothing while no layer waits. While a job runs it
    does nothing, because the pass after the job looks at every layer.
    """
    if _waiting and _job is None:
        notify()


def running() -> bool:
    """True while any layer is being refreshed."""
    return _job is not None


def running_on(node, pair: int = 0) -> bool:
    """True while a job is building *node*'s *pair*, for the panel and its Cancel button."""
    return (_job is not None and _job.uuid == node.uuid
            and _job.output == node.outputs[pair].identifier)


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
    if gpu_known() is True and not context_active():
        # Blender 5.3 has no GPU context between reading a file and the
        # event loop's next pass over a window. A build now would fail,
        # and the job would switch Auto Refresh off for a layer that is
        # fine. So wait for that pass, which comes soon in a window.
        # Nothing here touches the GPU in the meantime, not even closing
        # a job.
        return DEBOUNCE
    if _job is not None and _position_of(_job) is None:
        _drop(_job)
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
    """True when the pixels or settings *job* read have changed since it started.

    Strokes below are checked every tick, because comparing a counter is
    cheap and the addon's own pixel writes do not call `notify`. Setting
    changes need an IR build to detect, so they are only checked after a
    compile has called `notify`.
    """
    global _poked
    poked, _poked = _poked, False
    if _restarts.get(job.key, 0) >= RESTART_LIMIT:
        return False
    if job.painted_over():
        return True
    if not poked:
        return False
    moved = job.moved()
    # The IR build inside `moved` compiles the layer, which is still out
    # of date, so it calls `notify` again. That call brings no news. Left
    # set, it would make every tick pay for another IR build.
    _poked = False
    return moved


def _restart(job) -> None:
    """Drop *job*, because the pixels or settings it read have changed.

    A new build starts once things have been quiet for `DEBOUNCE`,
    counted from now. A stroke found by the counter came with no `notify`
    call to push the deadline back.
    """
    global _job, _deadline
    _job = None
    _deadline = max(_deadline, time.monotonic() + DEBOUNCE)
    job.close()
    _uncount(job)
    _restarts[job.key] = _restarts.get(job.key, 0) + 1
    log.debug("restarting the refresh of %s: what it read has moved", job.node_name)


def _drop(job) -> None:
    """Drop *job*, because its pair, its layer or its tree is gone.

    The build holds the node and the tree it started with. After either
    is removed, the next unit, and above all the commit, would write
    through a pointer to freed memory and crash Blender. A removed pair
    would leave the commit writing into another pair's state. Closing the
    build is safe, because that only frees its textures. A renamed layer
    looks gone too. It still needs a build, and the pass that follows
    finds it under its new name.
    """
    global _job
    _job = None
    job.close()
    _builds.pop(job.key, None)
    _restarts.pop(job.key, None)
    log.debug("dropped the refresh of %s: the layer is gone", job.node_name)


def _uncount(job) -> None:
    """Undo *job*'s count in `_builds`.

    The job was overtaken, which does not mean its pair failed to settle.
    """
    count = _builds.get(job.key, 0) - 1
    if count > 0:
        _builds[job.key] = count
    else:
        _builds.pop(job.key, None)


def _start():
    """The next pair to refresh, as a job, or None when there is none.

    Resolving allocates nothing, so a pair that cannot be refreshed says
    so here and the pass moves on to the next one. It keeps Auto Refresh
    on and waits. A change to its tree compiles it, which asks again. A
    change in the scene reaches `scene_changed` instead. Resolving is
    cheap enough to repeat after every change.
    """
    global _waiting
    if gpu_known() is not True:
        # Never be the first to call `gpu.init()`. It crashes instead of
        # raising where there is no usable driver, and a timer is the
        # worst place to find that out.
        return None
    waiting = False
    for tree, node, pair in _candidates():
        state = node.pairs[pair]
        try:
            plan = layer_plan.resolve_input(bpy.context, tree, node, pair)
        except Refused as refusal:
            _set_error(state, str(refusal))
            waiting = True
            continue
        if not plan.is_composite:
            _set_error(state, f"Update needed: filtering this needs a Cycles bake, "
                              f"because {plan.reason}")
            waiting = True
            continue
        key = pair_key(node, pair)
        if _builds.get(key, 0) >= BUILD_LIMIT:
            _give_up(Position(node, pair),
                     "Refreshing this layer did not settle; press Update to try again")
            continue
        _set_error(state, "")
        # Count at the start, not the end. The commit marks the tree, so
        # the compile that clears this count runs inside the build's own
        # last unit.
        _builds[key] = _builds.get(key, 0) + 1
        log.debug("refreshing %s (%s): %s", node.name, node.outputs[pair].name,
                  state.stale_reason or "not built yet")
        # This pass stops here, before it has seen every pair, so it can
        # only raise the flag. The pass after this build lowers it.
        _waiting = _waiting or waiting
        return _Job(tree, node, pair, plan)
    _waiting = waiting
    return None


def _candidates():
    """Filter pairs that are new or out of date, the bottom of each stack first.

    Bottom first, because a filter layer below an out-of-date one must be
    rebuilt first. Otherwise the upper layer filters pixels that are
    about to change, and goes out of date again as soon as they do.

    A switched-off layer is skipped. It renders as a pass-through, so a
    rebuild would spend the video memory and frame time of a full
    composite on pixels nothing can show. Switching a layer off is also
    the normal way to compare with and without it. The layer stays
    marked out of date, and switching it back on asks for the refresh.
    See `_enabled_changed` in `nodes/layers/filter_layer_node.py`, which
    cannot leave that to the compile it schedules.

    A layer the Update button is already building is skipped too. The
    build's first unit compiles, which calls `notify`, so a long Update
    would otherwise start a second build of the same layer. The commit
    compiles again, and that asks for a refresh if one is still needed.
    """
    for tree in ps_trees():
        # A linked tree is read again from its library whenever the file
        # opens, so a result built into it here would be thrown away, and
        # built again on every open.
        if not tree.is_editable:
            continue
        # The stack walks below are the expensive part, and most trees
        # hold no filter layer at all.
        if not any(getattr(node, 'ps_type', "") == 'FILTER' for node in tree.nodes):
            continue
        for channel in tree.channels:
            for item in reversed(tree.stack(channel.name)):
                node, pair = item.node, item.pair
                if getattr(node, 'ps_type', "") != 'FILTER':
                    continue
                if (node.enabled and node.auto_refresh and not node.lock_layer
                        and node.pairs[pair].needs_build
                        and not freshness.building(pair_key(node, pair))):
                    yield tree, node, pair


def _run(job):
    """Spend about `BUDGET` seconds on *job*, and return when to tick again."""
    global _job
    # Run at least one unit per tick, whatever the budget. A tick that
    # does nothing and asks to be called again would only spin.
    deadline = time.perf_counter() + BUDGET
    while True:
        try:
            next(job.steps)
        except StopIteration as done:
            _job = None
            _restarts.pop(job.key, None)
            # If its input changed, the build either was allowed to
            # finish past RESTART_LIMIT or was overtaken in its last unit.
            # It committed what it read, so the pair being out of date
            # says nothing about whether it can settle.
            if job.moved():
                _uncount(job)
            log.debug("refreshed %s", done.value.name)
            return DEBOUNCE
        except Refused as refusal:
            _job = None
            _restarts.pop(job.key, None)
            _give_up(_position_of(job), str(refusal))
            return DEBOUNCE
        except Exception:
            # For example a driver that fails mid-build. Log and stop
            # instead of retrying, because the same build would fail the
            # same way. The pair still shows its previous pixels.
            _job = None
            _restarts.pop(job.key, None)
            job.close()
            log.exception("could not refresh filter layer '%s'", job.node_name)
            _give_up(_position_of(job), "Refreshing this layer failed; see the system console")
            return DEBOUNCE
        if time.perf_counter() >= deadline:
            return 0.0


def _position_of(job) -> Position | None:
    """*job*'s layer and pair looked up again, or None when either is gone (PS-090)."""
    tree = bpy.data.node_groups.get(job.tree_name)
    if tree is None:
        return None
    node = tree.nodes.get(job.node_name)
    if node is None or getattr(node, 'uuid', "") != job.uuid:
        return None
    for pair, output in enumerate(node.outputs):
        if output.identifier == job.output:
            return Position(node, pair) if pair < len(node.pairs) else None
    return None


def _give_up(position: Position | None, message: str) -> None:
    """Stop refreshing the layer at *position* and say why, in the panel rather than a popup.

    Auto Refresh is the node's, so it goes off for every pair. The
    message goes on the pair that could not be refreshed.
    """
    if position is None:
        return
    node, pair = position
    # First, because switching Auto Refresh off clears every pair's message.
    node.auto_refresh = False
    _set_error(node.pairs[pair], message)
    log.debug("auto refresh off for %s: %s", node.name, message)


def _set_error(state, message: str) -> None:
    """Show *message* on the pair *state*, or clear it with ""."""
    # Writing an RNA property tags the tree and the materials using it,
    # so only write when the value actually changes.
    if state.derived_error != message:
        state.derived_error = message
