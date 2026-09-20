"""A filter layer refreshes itself after the layers below change (PS-057).

`filters.layer_job` is a `bpy.app.timers` callback, so the tick is driven
by hand here: `pump` sets the debounce aside and calls `_tick` until it
says it has nothing left to do. That is the whole job, minus waiting.

What is checked is the fencing more than the happy path. The auto path
must never start a Cycles bake, must leave a locked or opted-out layer
alone, must give the previous pixels back untouched when it is cancelled
mid-build, and must stop rather than rebuild forever if a build cannot
satisfy the check that asked for it.

These need a GPU context, as `test_filter_build.py` does.
"""
import os
import sys
import traceback

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, import_from, register_addon,  # noqa: E402
                     section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("compiler.core")
derived = import_from("filters.derived")
filters_core = import_from("filters.core")
layer_build = import_from("filters.layer_build")
layer_job = import_from("filters.layer_job")
undo_pixels = import_from("undo.pixels")
create_managed_image = import_from("compiler.bake").create_managed_image

SOLID = 'PaintSystemSolidColorLayerNode'
IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# Small enough that a whole build fits in a handful of ticks.
SIZE = '1024'


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def pump(limit=2000):
    """Run the job's timer by hand until it has nothing left to do.

    The debounce is set aside on every pass: a build's own commit marks
    the tree, and the compile that follows pushes the deadline out again.
    """
    for _ in range(limit):
        layer_job._deadline = 0.0
        if layer_job._tick() is None:
            return True
    return False


def stamp(node):
    image = node.derived_image
    return image[derived.BUILD_KEY] if derived.is_built(image) else ""


if available():
    try:
        section("a stroke below it")
        tree = bpy.data.node_groups.new("Auto", 'PaintSystemNodeTree')
        tree.initialize()
        with core.suspend_compile(tree):
            picture = tree.insert_layer_node(IMAGE)
            picture.image = create_managed_image("Auto Source", 8, 8)
            picture.image.pixels.foreach_set([0.3, 0.5, 0.7, 1.0] * 64)
            node = tree.insert_layer_node(FILTER)
            node.resolution = SIZE
        core.flush_now()
        layer_build.build_layer(bpy.context, tree, node)
        core.flush_now()
        was = stamp(node)
        check(node.stale_reason == "" and node.auto_refresh,
              "a built layer starts up to date, with Auto Refresh on")

        undo_pixels.write_pixels(picture.image, [0.8, 0.2, 0.1, 1.0] * 64)
        core.flush_now()
        check(node.stale_reason == "the pixels below changed",
              f"the write puts it out of date: {node.stale_reason!r}")
        check(bpy.app.timers.is_registered(layer_job._tick),
              "and the compile that saw it scheduled a refresh")

        check(pump(), "the refresh runs to the end")
        core.flush_now()
        check(stamp(node) != was, "it built new pixels")
        check(node.stale_reason == "" and not node.derived_stale_pixels,
              f"and the layer is up to date again: {node.stale_reason!r}")
        check(node.derived_error == "", "with nothing to report")

        section("a layer that did not ask")
        was = stamp(node)
        node.auto_refresh = False
        undo_pixels.write_pixels(picture.image, [0.1, 0.9, 0.4, 1.0] * 64)
        core.flush_now()
        pump()
        check(stamp(node) == was, "Auto Refresh off leaves the pixels alone")
        check(node.stale_reason == "the pixels below changed",
              "and the layer says it is out of date, waiting for Update")

        node.auto_refresh = True
        check(pump() and stamp(node) != was, "turning it back on refreshes")
        core.flush_now()

        section("a locked layer")
        was = stamp(node)
        node.lock_layer = True
        undo_pixels.write_pixels(picture.image, [0.2, 0.2, 0.9, 1.0] * 64)
        core.flush_now()
        pump()
        check(stamp(node) == was, "is not rebuilt behind the lock")
        node.lock_layer = False
        check(pump() and stamp(node) != was, "and unlocking lets it catch up")
        core.flush_now()

        section("cancelled part way")
        # One unit per tick, so the build is certain to still be running
        # after the first one. The commit is the last unit, which is what
        # makes stopping before it safe.
        budget, layer_job.BUDGET = layer_job.BUDGET, 0.0
        was = (stamp(node), tuple(node.derived_image.pixels[:4]))
        undo_pixels.write_pixels(picture.image, [0.9, 0.9, 0.1, 1.0] * 64)
        core.flush_now()
        layer_job._deadline = 0.0
        layer_job._tick()
        check(layer_job.running() and layer_job.running_on(node),
              "one tick leaves a job in flight on this layer")
        layer_job.cancel_all()
        check(not layer_job.running(), "cancel_all stops it")
        check((stamp(node), tuple(node.derived_image.pixels[:4])) == was,
              "and the layer keeps the pixels and the stamp it already had")
        check(not bpy.app.timers.is_registered(layer_job._tick),
              "with no timer left behind")
        layer_job.BUDGET = budget

        section("a refresh it must not attempt")
        # The auto path is hard-gated to the GPU composite. A Cycles bake
        # from a timer would lock the window for seconds with no way to
        # stop it, so the layer opts itself out and says why.
        inner = bpy.data.node_groups.new("Auto Inner", 'PaintSystemNodeTree')
        inner.initialize()
        group = tree.nodes.new('PaintSystemGroupLayerNode')
        group.node_tree = inner
        tree.links.new(group.outputs['Color'], picture.inputs['Color'])
        core.flush_now()
        was = stamp(node)
        check(pump(), "the pass finishes rather than retrying")
        check(stamp(node) == was, "nothing was built")
        check(not node.auto_refresh, "Auto Refresh turned itself off")
        check("Cycles bake" in node.derived_error,
              f"and the panel says why: {node.derived_error!r}")

        node.auto_refresh = True
        check(node.derived_error == "", "turning it back on clears the message")
        tree.links.remove(picture.inputs['Color'].links[0])
        tree.nodes.remove(group)
        core.flush_now()

        section("a refresh that does not settle")
        # A build whose stamp cannot satisfy the check that asked for it
        # would rebuild forever. The counter that stops that is cleared by
        # a compile finding the layer fresh, which real editing always
        # produces; here it is wound up by hand, because a build that
        # settles is the only kind this Paint System can produce.
        pump()
        core.flush_now()
        was = stamp(node)
        check(layer_job._builds.get(node.uuid) is None,
              "a build followed by a compile that saw it leaves no count behind")

        layer_job._builds[node.uuid] = layer_job.BUILD_LIMIT
        undo_pixels.write_pixels(picture.image, [0.4, 0.4, 0.4, 1.0] * 64)
        core.flush_now()
        check(pump(), "the pass finishes")
        check(stamp(node) == was, "without building again")
        check(not node.auto_refresh, "the layer stops refreshing itself")
        check("did not settle" in node.derived_error,
              f"and says so: {node.derived_error!r}")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

layer_job.cancel_all()
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()

finish("FILTER AUTO REFRESH TEST")
