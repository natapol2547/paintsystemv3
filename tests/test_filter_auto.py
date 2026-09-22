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
import tempfile
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


def quiet():
    """Take the refresh timer down, so that a later registration means something.

    Only Blender unregisters a timer, by seeing its callback return
    None, and `pump` never gives it the chance. Without this every check
    on `is_registered` would read True from whatever asked last.
    """
    if bpy.app.timers.is_registered(layer_job._tick):
        bpy.app.timers.unregister(layer_job._tick)


def stamp(node):
    image = node.derived_image
    return image[derived.BUILD_KEY] if derived.is_built(image) else ""


if available():
    try:
        section("a new layer")
        tree = bpy.data.node_groups.new("Auto", 'PaintSystemNodeTree')
        tree.initialize()
        quiet()
        with core.suspend_compile(tree):
            picture = tree.insert_layer_node(IMAGE)
            picture.image = create_managed_image("Auto Source", 8, 8)
            picture.image.pixels.foreach_set([0.3, 0.5, 0.7, 1.0] * 64)
            node = tree.insert_layer_node(FILTER)
            node.resolution = SIZE
        core.flush_now()
        check(not derived.is_built(node.derived_image) and node.stale_reason == "",
              "a new filter layer is unbuilt, which is not out of date")
        check(node.needs_build and bpy.app.timers.is_registered(layer_job._tick),
              "but it needs a build, and the compile that saw it scheduled one")
        check(pump() and derived.is_built(node.derived_image), "the job builds it")
        core.flush_now()
        check(not node.needs_build and node.derived_error == "",
              f"and the layer is up to date: {node.stale_reason!r}")

        section("a stroke below it")
        was = stamp(node)
        check(node.stale_reason == "" and node.auto_refresh,
              "a built layer starts up to date, with Auto Refresh on")

        quiet()
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

        section("a layer that is switched off")
        # It renders as a pass-through, so a rebuild would spend a whole
        # composite on pixels nothing can show. Switching a filter off to
        # compare with and without is the ordinary thing to do with one.
        was = stamp(node)
        node.enabled = False
        core.flush_now()
        # The timer has to be taken down by hand before either half of
        # this can mean anything. `pump` calls `_tick` as a plain
        # function, so Blender never sees the None return that would
        # unregister it, and a registration left over from the section
        # above would make "nothing asked" and "something asked" read
        # the same.
        quiet()
        undo_pixels.write_pixels(picture.image, [0.5, 0.1, 0.6, 1.0] * 64)
        core.flush_now()
        check(not bpy.app.timers.is_registered(layer_job._tick),
              "a stroke below it asks for nothing while it is switched off")
        pump()
        check(stamp(node) == was, "and it is not rebuilt while nothing can show it")
        check(node.stale_reason == "the pixels below changed",
              f"but it still knows it is out of date: {node.stale_reason!r}")
        check(node.auto_refresh, "and did not have to give up Auto Refresh to stay put")

        quiet()
        node.enabled = True
        core.flush_now()
        check(bpy.app.timers.is_registered(layer_job._tick),
              "switching it back on is what asks for the refresh")
        check(pump() and stamp(node) != was, "and it catches up")
        core.flush_now()

        section("a locked layer")
        was = stamp(node)
        node.lock_layer = True
        quiet()
        undo_pixels.write_pixels(picture.image, [0.2, 0.2, 0.9, 1.0] * 64)
        core.flush_now()
        pump()
        check(stamp(node) == was, "is not rebuilt behind the lock")

        quiet()
        node.lock_layer = False
        # The lock marks nothing -- it changes where a stroke goes, not
        # what the tree compiles to -- so unlocking has to ask for the
        # refresh itself rather than wait for a compile that never comes.
        check(bpy.app.timers.is_registered(layer_job._tick),
              "unlocking asks for the refresh on its own")
        check(pump() and stamp(node) != was, "and it catches up")
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

        section("overtaken part way")
        # A build reads the stack at its start and commits several ticks
        # later. A stroke or a setting that moves in between must leave
        # the layer out of date rather than under a stamp for pixels it
        # never saw -- the Update button, which drives the same steps
        # without any of the job's checks, included.
        budget, layer_job.BUDGET = layer_job.BUDGET, 0.0
        pump()
        core.flush_now()

        def begin():
            """Start a refresh and leave it one unit in."""
            layer_job._deadline = 0.0
            layer_job._tick()
            return layer_job.running_on(node)

        def as_built_now():
            """Whether the layer holds what an Update of it would build now."""
            got = stamp(node)
            layer_build.build_layer(bpy.context, tree, node)
            core.flush_now()
            return stamp(node) == got

        undo_pixels.write_pixels(picture.image, [0.6, 0.3, 0.2, 1.0] * 64)
        core.flush_now()
        check(begin(), "a stroke below starts a refresh")
        undo_pixels.write_pixels(picture.image, [0.2, 0.6, 0.3, 1.0] * 64)
        core.flush_now()
        layer_job._tick()
        check(not layer_job.running(), "a second stroke landing part way drops it")
        check(layer_job._builds.get(node.uuid) is None,
              "without counting it as a build that did not settle")
        check(pump(), "the refresh that follows runs to the end")
        core.flush_now()
        check(node.stale_reason == "", f"and leaves the layer up to date: {node.stale_reason!r}")
        check(as_built_now(), "with the pixels of the second stroke")

        node.invert_alpha = True
        core.flush_now()
        check(begin(), "a setting starts a refresh")
        node.invert_alpha = False
        core.flush_now()
        layer_job._tick()
        check(not layer_job.running(), "turning it back part way drops it")
        check(pump() and node.stale_reason == "",
              f"and the layer is up to date again: {node.stale_reason!r}")
        check(as_built_now(), "with the setting it ended on")

        # A compile while a job runs pokes it to check what moved. That
        # check compiles the layer, which is still stale and pokes again,
        # so only the first poke may cost an IR build.
        looks = []
        moved = layer_job._Job.moved
        layer_job._Job.moved = lambda job: looks.append(job.uuid) or moved(job)
        try:
            node.invert_alpha = True
            core.flush_now()
            check(begin(), "a setting starts a refresh")
            layer_job.notify()
            ticks = 0
            while layer_job.running() and ticks < 3:
                layer_job._tick()
                ticks += 1
        finally:
            layer_job._Job.moved = moved
        check(ticks == 3 and len(looks) == 1,
              f"one poke is one look at what moved ({len(looks)} in {ticks} ticks)")
        node.invert_alpha = False
        core.flush_now()
        check(pump() and node.stale_reason == "",
              f"and the layer settles afterwards: {node.stale_reason!r}")

        # Past the restart limit the build is let finish, which is where
        # the stamp has to be what was read rather than what is there.
        layer_job._restarts[node.uuid] = layer_job.RESTART_LIMIT
        undo_pixels.write_pixels(picture.image, [0.7, 0.7, 0.2, 1.0] * 64)
        core.flush_now()
        check(begin(), "a build past the restart limit starts")
        node.invert_alpha = True
        undo_pixels.write_pixels(picture.image, [0.1, 0.3, 0.8, 1.0] * 64)
        core.flush_now()
        while layer_job.running():
            layer_job._tick()
        check(node.derived_stale_pixels,
              "is let finish, and the stroke it missed still marks the layer")
        check(node.stale_reason == "the filter settings changed",
              f"while its stamp names the setting it read: {node.stale_reason!r}")
        check(layer_job._builds.get(node.uuid) is None,
              "and it is not counted as a build that did not settle")
        check(pump() and node.stale_reason == "" and not node.derived_stale_pixels,
              f"the refresh after it catches up: {node.stale_reason!r}")
        check(as_built_now(), "with the stroke and the setting it missed")

        # The Update path: the same steps driven by hand, with no job to
        # notice anything.
        run = layer_build.steps(bpy.context, tree, node)
        next(run)
        undo_pixels.write_pixels(picture.image, [0.3, 0.1, 0.5, 1.0] * 64)
        node.invert_alpha = False
        for _ in run:
            pass
        core.flush_now()
        check(node.derived_stale_pixels and node.stale_reason == "the filter settings changed",
              f"an Update overtaken part way leaves the layer out of date: {node.stale_reason!r}")
        # The settings are read with the stamp, not when the filter gets
        # to them a unit later: the pixels are those of the setting the
        # stamp names, which a build at that setting reproduces exactly.
        got = stamp(node)
        node.invert_alpha = True
        core.flush_now()
        layer_build.build_layer(bpy.context, tree, node)
        core.flush_now()
        check(stamp(node) == got, "and holds the pixels its stamp names")

        # Overtaken again and again, more often than BUILD_LIMIT allows a
        # build that cannot settle. None of them is that. The layer below
        # is what moves, and never back to where a build left it.
        layer_job._builds.clear()
        picture.opacity = 0.95
        core.flush_now()
        rounds = layer_job.BUILD_LIMIT + 2
        started = 0
        for index in range(rounds):
            if not begin():
                break
            started += 1
            picture.opacity = 0.9 - 0.1 * index
            core.flush_now()
            while layer_job.running():
                layer_job._tick()
        check(started == rounds, f"every nudge started a refresh: {started} of {rounds}")
        check(node.auto_refresh and node.derived_error == "",
              f"being overtaken {rounds} times in a row is not a refresh that "
              f"did not settle: {node.derived_error!r}")
        check(pump() and node.stale_reason == "", "and the last refresh catches up")
        check(as_built_now(), "with the stack it ended on")
        picture.opacity = 1.0
        core.flush_now()
        layer_job.BUDGET = budget

        section("a refresh it must not attempt")
        # The auto path is hard-gated to the GPU composite. A Cycles bake
        # from a timer would lock the window for seconds with no way to
        # stop it, so the layer waits and says why.
        inner = bpy.data.node_groups.new("Auto Inner", 'PaintSystemNodeTree')
        inner.initialize()
        group = tree.nodes.new('PaintSystemGroupLayerNode')
        group.node_tree = inner
        tree.links.new(group.outputs['Color'], picture.inputs['Color'])
        core.flush_now()
        was = stamp(node)
        check(pump(), "the pass finishes rather than retrying")
        check(stamp(node) == was, "nothing was built")
        check(node.auto_refresh, "Auto Refresh stays on, for when the stack changes")
        check("Cycles bake" in node.derived_error,
              f"and the panel says why: {node.derived_error!r}")

        node.auto_refresh = False
        check(node.derived_error == "", "switching Auto Refresh off clears the message")
        node.auto_refresh = True
        core.flush_now()
        check(pump() and "Cycles bake" in node.derived_error,
              f"and switching it back on asks again: {node.derived_error!r}")

        tree.links.remove(picture.inputs['Color'].links[0])
        tree.nodes.remove(group)
        core.flush_now()
        check(pump(), "taking the group away lets the job run")
        core.flush_now()
        check(not node.needs_build and node.derived_error == "",
              f"and clears the message: {node.derived_error!r}")

        section("a refusal about the scene")
        # A UV map the mesh does not have is about the mesh, not the
        # stack. Fixing the mesh compiles no tree, so the depsgraph
        # update is what has to ask again.
        uv_maps = bpy.context.view_layer.objects.active.data.uv_layers
        was = stamp(node)
        node.uv_map = "Auto Map"
        core.flush_now()
        check(pump() and stamp(node) == was and node.auto_refresh
              and "no UV map named 'Auto Map'" in node.derived_error,
              f"a UV map the mesh lacks waits and says why: {node.derived_error!r}")

        node.uv_map = ""
        core.flush_now()
        check(not node.needs_build and node.derived_error == "",
              f"putting it back clears the message with no build: {node.derived_error!r}")
        check(pump() and stamp(node) == was, "and nothing is rebuilt")

        node.uv_map = "Auto Map"
        core.flush_now()
        pump()
        quiet()
        uv_maps["UVMap"].name = "Auto Map"
        try:
            bpy.context.view_layer.update()
            check(bpy.app.timers.is_registered(layer_job._tick),
                  "renaming the mesh's UV map to match asks again")
            check(pump(), "the refresh runs to the end")
            core.flush_now()
            check(not node.needs_build and node.derived_error == "",
                  f"and the layer is built: {node.stale_reason!r}, {node.derived_error!r}")
        finally:
            uv_maps["Auto Map"].name = "UVMap"
        node.uv_map = ""
        core.flush_now()
        check(pump(), "going back to the render UV map rebuilds it")
        core.flush_now()

        section("a layer with nothing below")
        # A refusal is about the stack, and the stack can change. The layer
        # says why and waits, so a layer arriving below is enough.
        lonely = bpy.data.node_groups.new("Auto Lonely", 'PaintSystemNodeTree')
        lonely.initialize()
        with core.suspend_compile(lonely):
            alone = lonely.insert_layer_node(FILTER)
            alone.resolution = SIZE
        core.flush_now()
        check(pump(), "the pass finishes rather than retrying")
        check(not derived.is_built(alone.derived_image), "nothing was built")
        check(alone.auto_refresh, "Auto Refresh stays on")
        check("nothing below" in alone.derived_error,
              f"and the panel says why: {alone.derived_error!r}")

        with core.suspend_compile(lonely):
            lonely.insert_layer_node(SOLID)
            check(lonely.move_layer_node(alone, 'UP', 'SKIP'),
                  "moving the filter layer above a new layer")
        core.flush_now()
        check(pump() and derived.is_built(alone.derived_image), "is enough for the job to build it")
        core.flush_now()
        check(not alone.needs_build and alone.derived_error == "",
              f"and the message goes: {alone.derived_error!r}")

        section("Clear Result")
        # The job builds any layer with no pixels, so Clear has to switch
        # Auto Refresh off or the result would come straight back.
        bpy.context.scene.paint_system.active_node_tree = lonely
        lonely.nodes.active = alone
        check(bpy.ops.paint_system.clear_filter_result('EXEC_DEFAULT') == {'FINISHED'},
              "Clear Result drops the image")
        core.flush_now()
        check(pump() and not derived.is_built(alone.derived_image) and not alone.auto_refresh,
              "and turns Auto Refresh off, so the job leaves it cleared")
        bpy.data.node_groups.remove(lonely)
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

        section("Update after the job gave up")
        # The message says to press Update, so an Update that works turns
        # Auto Refresh back on as well as clearing the message.
        bpy.context.scene.paint_system.active_node_tree = tree
        tree.nodes.active = node
        check(bpy.ops.paint_system.rebuild_filter_layer('EXEC_DEFAULT') == {'FINISHED'},
              "Update builds the layer")
        check(node.auto_refresh and node.derived_error == "",
              f"and turns Auto Refresh back on: {node.auto_refresh}, {node.derived_error!r}")

        # A user who switched Auto Refresh off keeps it off.
        node.auto_refresh = False
        check(bpy.ops.paint_system.rebuild_filter_layer('EXEC_DEFAULT') == {'FINISHED'},
              "Update builds the layer again")
        check(not node.auto_refresh, "and leaves an Auto Refresh the user turned off alone")
        node.auto_refresh = True
        core.flush_now()

        section("an Update that takes a while")
        # Update's first unit compiles, and the compile calls `notify`.
        # The job must not start a second build of the layer Update is
        # building while it is still running.
        pump()
        undo_pixels.write_pixels(picture.image, [0.6, 0.6, 0.1, 1.0] * 64)
        core.flush_now()
        run = layer_build.steps(bpy.context, tree, node)
        next(run)
        layer_job._deadline = 0.0
        # One unit per tick, so a job that did start cannot also finish
        # inside this tick and look as if it never ran.
        budget, layer_job.BUDGET = layer_job.BUDGET, 0.0
        try:
            layer_job._tick()
            check(not layer_job.running_on(node),
                  "the job leaves alone a layer another build is running on")
        finally:
            layer_job.BUDGET = budget
        for _ in run:
            pass
        core.flush_now()
        check(node.stale_reason == "", f"and the Update brings it up to date: {node.stale_reason!r}")
        check(pump(), "with nothing left for the job to do")

        section("a stroke undone before the refresh")
        # Nothing can tell that the pixels below went back without reading
        # them, so the refresh still runs. It finds the pixels the layer
        # already has, and leaves the image alone.
        image = node.derived_image
        was = stamp(node)
        image.pixels[0]
        check(image.has_data, "the result is decoded to begin with")
        undo_pixels.write_pixels(picture.image, [0.1, 0.1, 0.1, 1.0] * 64)
        undo_pixels.write_pixels(picture.image, [0.6, 0.6, 0.1, 1.0] * 64)
        core.flush_now()
        check(node.stale_reason == "the pixels below changed",
              f"a stroke and its reverse leave the layer marked: {node.stale_reason!r}")
        check(pump(), "the refresh runs to the end")
        core.flush_now()
        check(node.stale_reason == "" and stamp(node) == was,
              f"and finds the pixels it already had: {node.stale_reason!r}")
        check(image.has_data, "without packing them again, which would free the decoded copy")
        check(layer_job._builds.get(node.uuid) is None,
              "and the compile after it still counts the layer as settled")

        section("undo after an automatic refresh")
        # The refresh writes after the stroke's undo step was pushed, so no
        # step holds its pixels. Whatever an undo or a redo restores, the
        # stamp and the pixels must agree, and the next refresh must bring
        # the layer back in line with the stack below.
        names = (tree.name, node.name, picture.name)

        def refetch():
            tree = bpy.data.node_groups[names[0]]
            return tree, tree.nodes[names[1]], tree.nodes[names[2]]

        def result(node):
            return stamp(node), tuple(node.derived_image.pixels[:4])

        before = result(node)
        bpy.ops.ed.undo_push(message="before the stroke")
        undo_pixels.write_pixels(picture.image, [0.8, 0.2, 0.1, 1.0] * 64)
        core.flush_now()
        check(pump(), "a stroke below is refreshed")
        core.flush_now()
        after = result(node)
        check(after[0] != before[0], "with new pixels")

        for label, step, want in (("undo", bpy.ops.ed.undo, before),
                                  ("redo", bpy.ops.ed.redo, after)):
            step()
            # What the window's depsgraph evaluation does after an undo.
            bpy.context.view_layer.update()
            tree, node, picture = refetch()
            check(result(node) in (before, after),
                  f"after {label} the stamp and the pixels agree: {result(node)[0][:8]}")
            check(pump(), f"the refresh after {label} runs to the end")
            core.flush_now()
            check(result(node) == want and node.stale_reason == "",
                  f"and matches the stack below again: {node.stale_reason!r}")

        section("saved and reopened")
        folder = tempfile.mkdtemp(prefix="ps_filter_auto_")
        tree.use_fake_user = True

        def reopen(name):
            # Packed, so the pixels below come back as they were saved.
            picture.image.pack()
            path = os.path.join(folder, name)
            check(bpy.ops.wm.save_as_mainfile(filepath=path) == {'FINISHED'}, f"{name} saves")
            check(bpy.ops.wm.open_mainfile(filepath=path) == {'FINISHED'}, "and reopens")
            bpy.context.view_layer.update()
            return refetch()

        was = stamp(node)
        tree, node, picture = reopen("fresh.blend")
        check(node.stale_reason == "", f"a layer saved up to date reopens up to date: "
                                       f"{node.stale_reason!r}")
        check(pump() and stamp(node) == was, "and the job has nothing to build")

        # A background session reopens with undo off until the first push.
        # A windowed one reopens with no window in the script's context,
        # and the push needs one to find the screen.
        windows = bpy.context.window_manager.windows
        if len(windows):
            with bpy.context.temp_override(window=windows[0]):
                bpy.ops.ed.undo_push(message="reopened")
        else:
            bpy.ops.ed.undo_push(message="reopened")
        undo_pixels.write_pixels(picture.image, [0.2, 0.7, 0.3, 1.0] * 64)
        core.flush_now()
        layer_job.cancel_all()
        tree, node, picture = reopen("stale.blend")
        check(node.stale_reason == "the pixels below changed",
              f"a layer saved out of date reopens out of date: {node.stale_reason!r}")
        check(pump() and stamp(node) != was and node.stale_reason == "",
              f"and the job brings it up to date: {node.stale_reason!r}")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

layer_job.cancel_all()
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()

finish("FILTER AUTO REFRESH TEST")
