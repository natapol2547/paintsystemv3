"""The live selection follows the active layer and keeps its outputs in step (PS-091).

`selection.session` decides what the active tree's selection applies to,
compares a state per sync, builds the mask once and remembers failures.
Background Blender runs no timers, so these tests call `session.sync()`
or `session._tick()` where the window loop would run the scheduled tick,
and check that the tick is scheduled. A sync that reaches the consumers
ends by tagging a redraw, so wrapping `session.redraw_paint_views` counts them.
"""
import os
import sys
import tempfile
from types import SimpleNamespace

import bpy
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section, since, skip  # noqa: E402

register_addon()
core = import_from("gpu_passes.core")
raster = import_from("selection.raster")
session = import_from("selection.session")
stencil = import_from("selection.stencil")
selection_ops = import_from("ops.selection_ops")
ps_context = import_from("context")

reaches = []
_redraw_paint_views = session.redraw_paint_views


def _counting_redraw(window_manager):
    reaches.append(session._last)
    _redraw_paint_views(window_manager)


session.redraw_paint_views = _counting_redraw


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def run(op, **props):
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result


def pending():
    return bpy.app.timers.is_registered(session._tick)


def cancel_tick():
    """Drop a scheduled tick, as if the window loop had not run it yet."""
    if pending():
        bpy.app.timers.unregister(session._tick)
    session._pending_force = False


def setup():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    run(bpy.ops.paint_system.setup_material)
    run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    t = tree()
    big = t.nodes.active.name
    run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    small = t.nodes.active
    small.image = bpy.data.images.new("PS Session Small", 256, 128)
    small_name = small.name
    run(bpy.ops.paint_system.add_layer, layer_type='SOLID_COLOR')
    solid = t.nodes.active.name
    return big, small_name, solid


BIG, SMALL, SOLID = setup()
# Starts the background GPU context where one exists (5.2 and later).
HAS_GPU = core.gpu_available()


def select(name):
    t = tree()
    t.active_layer_index = t.nodes.find(name)


def test_target_follows_the_active_layer():
    section("the selection applies to the active layer")
    t = tree()
    t.selection.clear()
    select(SOLID)
    target, reason = session.resolve_target(bpy.context)
    check(target is None and reason == session.NO_IMAGE, f"a solid colour layer has no target ({reason})")
    select(BIG)
    target, reason = session.resolve_target(bpy.context)
    check(target is not None and target.size == (1024, 1024) and target.uv_map == "UVMap"
          and target.object == cube() and target.tile == 1001,
          f"an image layer is the target at its size, through its UV map ({target and target.size})")
    select(SMALL)
    target, _ = session.resolve_target(bpy.context)
    check(target.size == (256, 128), f"switching layers moves the target to the other size ({target.size})")

    t.nodes[SMALL].uv_map = "Missing"
    check(session.resolve_target(bpy.context)[1] == session.NO_UV_MAP, "a UV map the mesh lacks is reported")
    t.nodes[SMALL].uv_map = ""
    tiled = bpy.data.images.new("PS Session Tiled", 64, 64, tiled=True)
    t.nodes[SMALL].image, small_image = tiled, t.nodes[SMALL].image
    check(session.resolve_target(bpy.context)[1] == session.UDIM, "a UDIM image is reported as unsupported")
    t.selection.add_op('ALL')
    state = session.sync(force=True)
    check(state.selected and state.reason == session.UDIM and session.label(state) == "UDIM layers are not supported yet",
          f"a selection on a UDIM layer carries the reason and its label ({state.reason})")
    t.selection.clear()
    t.nodes[SMALL].image = small_image
    bpy.data.images.remove(tiled)
    check(session.label(session.State(selected=True, reason='UNLABELLED', message="Something is wrong"))
          == "Something is wrong", "a reason without a label falls back to the state's message")


def test_notify_schedules_one_tick():
    section("notify schedules one tick")
    cancel_tick()
    for _ in range(3):
        session.notify()
    check(pending(), "notify registers the tick")
    check(not session._pending_force, "a plain notify does not force")
    session.notify(force=True)
    session.notify()
    check(session._pending_force, "force holds until the tick runs, whatever later calls pass")
    cancel_tick()
    select(BIG)
    check(pending(), "a layer switch through update_active_image notifies")
    cancel_tick()

    select(BIG)
    tree().selection.clear()
    session.sync(force=True)
    reaches.clear()
    session.sync()
    check(not reaches, "an unchanged state does not reach the consumers")
    session.notify(force=True)
    unchanged = session.current()
    session._tick()
    check(len(reaches) == 1 and session.current() == unchanged and not session._pending_force,
          f"a forced tick reaches the consumers with an equal state ({len(reaches)})")
    cancel_tick()


def test_sync_compares_state():
    section("sync reconciles only when the state changed")
    select(BIG)
    t = tree()
    t.selection.clear()
    reaches.clear()
    empty = session.sync(force=True)
    check(not empty.selected and len(reaches) == 1, "the first sync after a forced one reaches the consumers")
    session.sync()
    check(len(reaches) == 1, "a second sync with nothing changed does not")

    t.selection.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.6)], feather=4.0)
    state = session.sync()
    check(state.selected and state.size == (1024, 1024)
          and state.digest == t.selection.prefix_digests(1024, 1024, 1001, surface_key=raster.view_key)[-1],
          "an edit changes the digest and syncs")
    check(len(reaches) == 2, f"and reaches the consumers once ({len(reaches)})")
    select(SMALL)
    moved = session.sync()
    check(moved.size == (256, 128) and moved.digest != state.digest and len(reaches) == 3,
          "switching to a layer of another size is a new state with a new digest")
    check(moved.image_uid == t.nodes[SMALL].image.session_uid and moved.object_uid == cube().session_uid
          and moved.tree_uid == t.session_uid and moved.scene_uid == bpy.context.scene.session_uid,
          "the state names the scene, tree, object and image it was built for")

    other = bpy.data.scenes.new("PS Session Other Scene")
    try:
        with bpy.context.temp_override(scene=other):
            elsewhere = session.sync()
        check(elsewhere.scene_uid == other.session_uid and elsewhere != moved and len(reaches) == 4,
              "another scene is a new state")
    finally:
        bpy.data.scenes.remove(other)
    session.sync()

    if not HAS_GPU:
        check(moved.reason == 'NO_GPU' and moved.message == raster.MESSAGES['NO_GPU'],
              f"without a GPU context the state says why ({moved.reason})")
        count = len(reaches)
        session.sync()
        check(len(reaches) == count, "and a failed build that fails the same way again does not reach the consumers")
        skip("mask builds need a GPU context (background Blender before 5.2)")
        return
    check(moved.active and raster.peek_mask(t.selection, (256, 128)) is not None, "sync built the mask")
    raster.invalidate()
    count = len(reaches)
    again = session.sync()
    check(again == moved and raster.peek_mask(t.selection, (256, 128)) is not None and len(reaches) == count + 1,
          "an evicted mask is rebuilt even though the state is unchanged, and the consumers hear of it")


def test_failures_are_remembered():
    section("a mask that cannot be built is not tried again for the same digest")
    session.forget_failures()
    select(BIG)
    t = tree()
    t.selection.clear()
    t.selection.add_op('LASSO', points=[(0.1, 0.1), (0.9, 0.1), (0.5, 0.9)])
    calls = []
    original_get_mask = raster.get_mask
    original_known = core.gpu_known

    def failing(selection, size, tile=1001, reason='TOO_COMPLEX'):
        calls.append(reason)
        raise raster.MaskUnavailable(reason, "The lasso outline is too complex to build", 0)

    raster.get_mask = failing
    core.gpu_known = lambda: True
    try:
        first = session.sync()
        second = session.sync()
        third = session.sync(force=True)
        check(first.reason == 'TOO_COMPLEX' and first.message == "The lasso outline is too complex to build"
              and session.label(first) == "Lasso too complex",
              f"the state carries the reason, the message and a label ({first.reason})")
        check(len(calls) == 1 and second == first and third == first,
              f"the build ran once across syncs and a forced sync ({len(calls)})")

        t.selection.add_op('INVERT')
        raster.get_mask = lambda selection, size, tile=1001: failing(selection, size, tile, 'GPU_ERROR')
        calls.clear()
        cancel_tick()
        results = []
        labels = []
        for _ in range(session.RETRY_LIMIT):
            results.append(session._tick())
            labels.append(session.label(session.current()))
        check(results == [session.RETRY_INTERVAL] * (session.RETRY_LIMIT - 1) + [None]
              and len(calls) == session.RETRY_LIMIT,
              f"GPU_ERROR is tried {session.RETRY_LIMIT} times and then left up ({results}, {len(calls)} builds)")
        check(session.current().reason == 'GPU_ERROR', "the state says so")
        check(labels == ["GPU error, retrying"] * (session.RETRY_LIMIT - 1) + [session.GPU_ERROR_GIVEN_UP],
              f"the label stops promising a retry once the tries run out ({labels})")
        check(session.current().digest not in session._failures, "GPU_ERROR is not remembered as a failure")
    finally:
        raster.get_mask = original_get_mask
        core.gpu_known = original_known
        session.forget_failures()
        session.sync(force=True)


def add_view_box(selection, obj, mode='REPLACE'):
    """A `VIEW` box over the middle of a 64 x 64 top view of *obj*, through its UVMap."""
    view = Matrix.Translation((0.0, 0.0, -10.0)) @ obj.matrix_world
    projection = Matrix.Identity(4)
    projection[0][0] = projection[1][1] = 0.25
    projection[2][2] = -2.0 / 19.0
    projection[2][3] = -21.0 / 19.0
    return selection.add_op('BOX', mode, space='VIEW', points=[(16.2, 16.3), (47.7, 47.6)], region_size=(64, 64),
                            view_matrix=[value for column in view.col for value in column],
                            projection_matrix=[value for column in projection.col for value in column],
                            object=obj, uv_map="UVMap")


def test_view_selection_digest_and_reasons():
    section("a selection drawn in the 3D view keys its digest by the surface and is not remembered as failed")
    session.forget_failures()
    select(BIG)
    t = tree()
    t.selection.clear()
    calls = []
    view_key = raster.view_key

    def counting(op, peek=False):
        calls.append(op.kind)
        return view_key(op, peek)

    raster.view_key = counting
    try:
        t.selection.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.6)])
        t.selection.add_op('INVERT')
        session.sync()
        check(not calls, f"a selection drawn in UV space calls no surface key provider ({len(calls)} calls)")
    finally:
        raster.view_key = view_key

    mesh = cube().data.copy()
    other = bpy.data.objects.new("PS Session View Object", mesh)
    bpy.context.scene.collection.objects.link(other)
    bpy.context.view_layer.update()
    t.selection.clear()
    op = add_view_box(t.selection, other)
    state = session.sync()
    check(state.digest == t.selection.prefix_digests(1024, 1024, 1001, surface_key=raster.view_key)[-1]
          and state.digest != t.selection.prefix_digests(1024, 1024, 1001)[-1],
          "the state's digest includes the key of the surface the op was drawn on")
    bpy.data.objects.remove(other)
    bpy.data.meshes.remove(mesh)
    state = session.sync()
    if not HAS_GPU:
        check(state.reason == 'NO_GPU', f"without a GPU context the state says so first ({state.reason})")
        skip("building a VIEW selection needs a GPU context (background Blender before 5.2)")
        t.selection.clear()
        session.sync(force=True)
        return
    check(state.reason == 'SURFACE' and session.label(state) == "Selection's object or UV map is gone"
          and not session._failures,
          f"a deleted object gives SURFACE with its label, and nothing is remembered ({state.reason})")
    op.object = cube()
    state = session.sync()
    check(state.reason == '' and state.active, f"pointing the op at an object clears it on the next sync "
                                                f"({state.reason})")
    op.region_size = (0, 0)
    state = session.sync()
    check(state.reason == 'VIEW' and session.label(state) == "Selection's view is invalid" and not session._failures,
          f"an empty region gives VIEW with its label, not remembered ({state.reason})")
    t.selection.clear()
    session.sync(force=True)


def test_empty_mask_is_no_selection():
    section("a mask that selects nothing counts as no selection")
    if not HAS_GPU:
        skip("mask builds need a GPU context (background Blender before 5.2)")
        return
    select(BIG)
    t = tree()
    t.selection.clear()
    session.sync(force=True)
    # Beyond the tile by more than the soft edge: nothing is covered.
    t.selection.add_op('BOX', points=[(1.5, 1.5), (1.8, 1.8)])
    reaches.clear()
    state = session.sync()
    check(state.selected and state.empty and not state.reason and not state.active and len(t.selection.ops) == 1,
          f"the state is empty but selected, with no reason and the op kept ({state.empty}, {state.reason!r})")
    check(session.label(state) == "Nothing selected" and len(reaches) == 1,
          f"the label says so, and the consumers hear of it ({session.label(state)!r})")
    mask = raster.peek_mask(t.selection, state.size)
    reads = []
    read_bytes = raster.SelectionMask.read_bytes

    def counting(self):
        reads.append(self.key)
        return read_bytes(self)

    raster.SelectionMask.read_bytes = counting
    try:
        again = session.sync()
        forced = session.sync(force=True)
        check(again == state and forced == state and mask.is_empty() and not reads,
              f"further syncs keep it empty without reading the mask back again ({len(reads)} reads)")
        raster.invalidate()
        rebuilt = session.sync()
        check(rebuilt == state and len(reads) == 1, f"a rebuilt mask is read back once ({len(reads)} reads)")
    finally:
        raster.SelectionMask.read_bytes = read_bytes

    t.selection.add_op('BOX', 'ADD', points=[(0.2, 0.2), (0.4, 0.4)])
    state = session.sync()
    check(not state.empty and state.active and session.label(state) != "Nothing selected",
          "adding a shape that covers texels makes it a selection again")
    t.selection.add_op('BOX', 'SUBTRACT', points=[(0.0, 0.0), (1.0, 1.0)])
    state = session.sync()
    check(state.empty and not state.active, "subtracting everything empties it again")
    t.selection.clear()
    state = session.sync()
    check(not state.selected and not state.empty, "clearing the selection is no selection, not an empty one")


def test_select_all_operator():
    section("select all, none and invert")
    expected = {'REGISTER', 'UNDO'} if since(5, 1) else {'REGISTER'}
    check(selection_ops.UNDO_OPTIONS == expected
          and selection_ops.PAINTSYSTEM_OT_select_all.bl_options == expected,
          f"the undo flag is only set from 5.1 on ({sorted(selection_ops.UNDO_OPTIONS)})")
    select(BIG)
    t = tree()
    t.selection.clear()
    cancel_tick()
    check(run(bpy.ops.paint_system.select_all, action='DESELECT') == {'CANCELLED'},
          "deselecting nothing changes nothing and pushes no undo step")
    check(run(bpy.ops.paint_system.select_all, action='SELECT') == {'FINISHED'}
          and [op.kind for op in t.selection.ops] == ['ALL'], "select all stores ALL")
    check(pending(), "the operator notifies")
    check(run(bpy.ops.paint_system.select_all, action='SELECT') == {'CANCELLED'}, "selecting all again is cancelled")
    t.selection.clear()
    t.selection.add_op('BOX', points=[(0.1, 0.1), (0.6, 0.6)])
    before = t.selection.prefix_digests(64, 64, 1001)[-1]
    run(bpy.ops.paint_system.select_all, action='INVERT')
    check([op.kind for op in t.selection.ops] == ['BOX', 'INVERT'], "invert appends INVERT")
    run(bpy.ops.paint_system.select_all, action='INVERT')
    check([op.kind for op in t.selection.ops] == ['BOX'] and t.selection.prefix_digests(64, 64, 1001)[-1] == before,
          "inverting again drops it, back to the same digest and cached mask")
    check(run(bpy.ops.paint_system.select_all, action='DESELECT') == {'FINISHED'} and t.selection.is_empty,
          "deselect clears the ops")
    run(bpy.ops.paint_system.select_all, action='SELECT')
    run(bpy.ops.paint_system.select_all, action='INVERT')
    check(t.selection.is_empty, "inverting all leaves no selection, not one that blocks all painting")
    properties = bpy.context.window_manager.operator_properties_last("paint_system.select_all")
    descriptions = {}
    for action in ('SELECT', 'DESELECT', 'INVERT'):
        properties.action = action
        descriptions[action] = selection_ops.PAINTSYSTEM_OT_select_all.description(bpy.context, properties)
    check(descriptions == {'SELECT': "Select the whole layer", 'DESELECT': "Clear the selection",
                           'INVERT': "Invert the selection"},
          f"the tooltip describes the chosen action {descriptions}")
    cancel_tick()


def test_select_all_modes():
    section("select all runs only where its undo step restores it")
    select(BIG)
    t = tree()
    t.selection.clear()
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        check(not bpy.ops.paint_system.select_all.poll(), f"it does not run in {bpy.context.mode}")
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    check(bpy.ops.paint_system.select_all.poll(), "it runs in Object mode")

    pushes = []
    fake_bpy = SimpleNamespace(ops=SimpleNamespace(ed=SimpleNamespace(undo_push=lambda message: pushes.append(message))))
    real_bpy, selection_ops.bpy = selection_ops.bpy, fake_bpy
    try:
        for mode in ('OBJECT', 'PAINT_TEXTURE'):
            selection_ops.push_undo(SimpleNamespace(mode=mode), mode)
    finally:
        selection_ops.bpy = real_bpy
    expected = [] if since(5, 1) else ['OBJECT']
    check(pushes == expected,
          f"push_undo pushes only before 5.1 and never in texture paint mode, where it would be dead ({pushes})")

    restores = {mode: selection_ops.undo_restores_data(SimpleNamespace(mode=mode))
                for mode in ('OBJECT', 'PAINT_TEXTURE', 'EDIT_MESH')}
    check(restores == {'OBJECT': True, 'PAINT_TEXTURE': since(5, 1), 'EDIT_MESH': False},
          f"undo restores a data edit in Object mode, in texture paint mode from 5.1, never in Edit mode ({restores})")


def test_consumer_failure_is_retried():
    section("a consumer that raises is reached again by the next sync")
    select(BIG)
    t = tree()
    t.selection.clear()
    session.sync(force=True)
    t.selection.add_op('ALL')
    original = stencil.sync
    calls = []

    def failing(state, target):
        calls.append(state)
        raise OSError("No space left on device")

    stencil.sync = failing
    try:
        reaches.clear()
        state = session.sync()
        check(len(calls) == 1 and len(reaches) == 1 and session.current() == state,
              f"the failure is logged, not raised, and the sync still ends ({len(calls)} calls)")
    finally:
        stencil.sync = original
    calls.clear()

    def counting(state, target):
        calls.append(state)
        original(state, target)

    stencil.sync = counting
    try:
        session.sync()
        session.sync()
    finally:
        stencil.sync = original
    check(len(calls) == 1, f"the next sync with an equal state reaches it once more, and no further ({len(calls)})")
    t.selection.clear()
    session.sync(force=True)


def test_uv_map_rename_notifies():
    section("renaming the layer's UV map reaches the session through the depsgraph")
    select(BIG)
    t = tree()
    t.selection.clear()
    t.selection.add_op('ALL')
    layer = t.nodes[BIG]
    layer.uv_map = "UVMap"
    bpy.context.view_layer.update()
    session.sync(force=True)
    cancel_tick()
    uv_layers = cube().data.uv_layers
    try:
        uv_layers["UVMap"].name = "PS Session Renamed"
        bpy.context.view_layer.update()
        scheduled = pending()
        cancel_tick()
        state = session.sync()
        check(scheduled and state.reason == session.NO_UV_MAP and session.label(state) == "Layer's UV map is missing",
              f"a rename schedules a tick that reports NO_UV_MAP ({scheduled}, {state.reason})")
        uv_layers["PS Session Renamed"].name = "UVMap"
        bpy.context.view_layer.update()
        scheduled = pending()
        cancel_tick()
        state = session.sync()
        check(scheduled and state.reason != session.NO_UV_MAP and state.uv_map == "UVMap",
              f"renaming it back schedules a tick that clears it ({scheduled}, {state.reason})")
    finally:
        if "PS Session Renamed" in uv_layers:
            uv_layers["PS Session Renamed"].name = "UVMap"
        layer.uv_map = ""
        t.selection.clear()
        cancel_tick()


def test_update_active_image_without_a_tree_notifies():
    section("update_active_image notifies even when there is no tree")
    obj = cube()
    scene_settings = bpy.context.scene.paint_system
    saved_tree = scene_settings.active_node_tree
    plain = bpy.data.materials.new("PS Session Plain")
    obj.data.materials.append(plain)
    obj.active_material_index = len(obj.material_slots) - 1
    scene_settings.active_node_tree = None
    bpy.context.view_layer.update()
    try:
        check(ps_context.get_active_tree(bpy.context) is None, "the active material has no tree")
        cancel_tick()
        ps_context.update_active_image(bpy.context)
        check(pending(), "the tick is scheduled")
        cancel_tick()
        state = session.sync()
        check(state.tree_uid == 0 and not state.selected, "and it syncs a state without a tree")
    finally:
        obj.active_material_index = 0
        obj.data.materials.pop(index=len(obj.material_slots) - 1)
        bpy.data.materials.remove(plain)
        scene_settings.active_node_tree = saved_tree
        bpy.context.view_layer.update()
        cancel_tick()
        session.sync(force=True)


def test_undo_and_load_force_a_sync():
    section("undo, redo and load force a sync")
    pixels = import_from("undo.pixels")
    pixels.ensure_undo_stack()
    select(BIG)
    tree().selection.clear()
    bpy.ops.ed.undo_push(message="before select")
    run(bpy.ops.paint_system.select_all, action='SELECT')
    session.sync()
    check(session.current().selected, "synced with a selection")
    cancel_tick()
    bpy.ops.ed.undo()
    check(tree().selection.is_empty, "undo restores the empty selection")
    check(pending() and session._pending_force, "undo_post schedules a forced sync")
    reaches.clear()
    session._tick()
    check(not session.current().selected and len(reaches) == 1, "which syncs the restored selection")
    cancel_tick()
    bpy.ops.ed.redo()
    check(not tree().selection.is_empty and pending() and session._pending_force, "redo does the same")
    session._tick()
    check(session.current().selected, "and the selection is live again")

    session._failures[b"stale"] = ('TOO_COMPLEX', "stale")
    path = os.path.join(tempfile.mkdtemp(prefix="ps_session_"), "session.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    cancel_tick()
    bpy.ops.wm.open_mainfile(filepath=path)
    check(pending() and session._pending_force and not session._failures,
          "loading a file forgets failures and schedules a forced sync")
    reaches.clear()
    session._tick()
    check(session.current().selected and len(reaches) == 1, "the saved selection is live again after the tick")
    cancel_tick()


guarded(test_target_follows_the_active_layer)
guarded(test_notify_schedules_one_tick)
guarded(test_sync_compares_state)
guarded(test_failures_are_remembered)
guarded(test_view_selection_digest_and_reasons)
guarded(test_empty_mask_is_no_selection)
guarded(test_select_all_operator)
guarded(test_select_all_modes)
guarded(test_consumer_failure_is_retried)
guarded(test_uv_map_rename_notifies)
guarded(test_update_active_image_without_a_tree_notifies)
guarded(test_undo_and_load_force_a_sync)
# GPU textures still referenced when Python exits are freed after the GPU context (see test_selection_raster).
session.release()
raster.release()
import_from("gpu_passes.texel_map").release()
finish("SELECTION SESSION TEST")
