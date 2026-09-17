"""Surface keys follow the content of an object's evaluated mesh (PS-092, PS-093).

`gpu_passes.surface` needs no GPU, so everything here runs headless on
every version. Background Blender runs no timers: the tests call
`surface._tick()` where the window loop would. A tick that finds a
changed key ends in `surface._surfaces_changed`, which the tests wrap to
count.
"""
import os
import sys
import types

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section  # noqa: E402

register_addon()
surface = import_from("gpu_passes.surface")
texel_map = import_from("gpu_passes.texel_map")
handlers = import_from("handlers.node_tree_handlers")
session = import_from("selection.session")

UV = "UVMap"

changes = []
_surfaces_changed = surface._surfaces_changed


def _counting_surfaces_changed():
    changes.append(True)
    _surfaces_changed()


surface._surfaces_changed = _counting_surfaces_changed


def make_cube(name, location=(0.0, 0.0, 0.0)):
    """A fresh factory cube with its own mesh."""
    old = bpy.data.objects.get(name)
    if old is not None:
        bpy.data.objects.remove(old)
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name
    return obj


def update():
    bpy.context.view_layer.update()


def key(obj, uv_map=UV):
    return surface.resolve_key(obj, uv_map)


def entry(obj, uv_map=UV):
    """The entry of *obj* and *uv_map* on the context's view layer, or None."""
    layer = surface._layer(bpy.context.evaluated_depsgraph_get())
    return surface._entries.get((obj.session_uid, uv_map, layer))


def changed_by(obj, edit, label):
    """Resolve, apply *edit*, mark the object suspect as the handler would, and check the key changed."""
    before = key(obj)
    edit()
    update()
    surface.mark_suspect(obj.session_uid)
    after = key(obj)
    check(before is not None and after is not None and before != after, f"{label} changes the key")


def cancel_timers():
    for fn in (surface._tick, session._tick):
        if bpy.app.timers.is_registered(fn):
            bpy.app.timers.unregister(fn)
    surface._requests.clear()
    session._pending_force = False


def test_stable_without_content_change():
    section("the key holds while the content does")
    surface.forget()
    obj = make_cube("PS Surface Stable")
    first = key(obj)
    check(first is not None and len(first) == surface.KEY_SIZE, f"a mesh with a UV map has a {surface.KEY_SIZE}-byte key")
    update()
    surface.mark_suspect(obj.session_uid)
    check(key(obj) == first, "re-evaluating after a suspect mark gives the same key")
    obj.data.update()
    update()
    surface.mark_suspect(obj.session_uid)
    check(key(obj) == first, "a data update with no change gives the same key")
    obj.location = (3.0, 1.0, 0.0)
    obj.rotation_euler = (0.3, 0.0, 0.2)
    update()
    surface.mark_suspect(obj.session_uid)
    check(key(obj) == first, "moving and rotating the object keeps the key")

    # 4.2 to 4.5 give a subdivided grid slightly different UVs on each evaluation.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40)
    obj = bpy.context.object
    obj.modifiers.new("PS Surface Subsurf", 'SUBSURF')
    update()
    with_subsurf = key(obj)
    subsurf_entry = entry(obj)
    token = subsurf_entry.token
    obj.update_tag(refresh={'DATA'})
    update()
    surface.mark_suspect(obj.session_uid)
    check(key(obj) == with_subsurf,
          "re-evaluating a Subdivision Surface keeps the key "
          f"(token {'changed' if subsurf_entry.token != token else 'held'})")
    uv_diff = 0.0
    for _ in range(3):
        stored = subsurf_entry.arrays["uv"]
        obj.update_tag(refresh={'DATA'})
        update()
        mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        fresh = np.empty(len(mesh.loops) * 2, np.float32)
        mesh.attributes[UV].data.foreach_get('vector', fresh)
        uv_diff = max(uv_diff, float(np.abs(fresh - stored).max()))
        surface.mark_suspect(obj.session_uid)
        if key(obj) != with_subsurf:
            break
    check(key(obj) == with_subsurf and uv_diff <= surface.UV_TOLERANCE,
          f"UVs within UV_TOLERANCE keep the key (largest difference {uv_diff:.1e})")


def test_content_changes():
    section("a change to what caches are built from changes the key")
    surface.forget()
    obj = make_cube("PS Surface Content")
    mesh = obj.data

    def move_vertex():
        mesh.vertices[0].co.x += 0.25
        mesh.update()

    changed_by(obj, move_vertex, "moving one vertex")

    def move_uv():
        mesh.uv_layers[UV].uv[0].vector = (0.9, 0.1)
        mesh.update()

    changed_by(obj, move_uv, "moving one UV")
    modifier = None

    def add_subsurf():
        nonlocal modifier
        modifier = obj.modifiers.new("PS Surface Subsurf", 'SUBSURF')
        modifier.levels = 1

    changed_by(obj, add_subsurf, "a Subdivision Surface modifier")

    def subsurf_levels():
        modifier.levels = 2

    changed_by(obj, subsurf_levels, "subdivision levels 1 to 2")
    obj.modifiers.remove(modifier)
    update()

    def material_index():
        if len(mesh.materials) < 2:
            mesh.materials.append(None)
            mesh.materials.append(None)
        mesh.polygons[0].material_index = 1
        mesh.update()

    changed_by(obj, material_index, "a material index")

    def sharp_face():
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        mesh.update()

    changed_by(obj, sharp_face, "shading smooth")

    def sharp_edge():
        mesh.edges[3].use_edge_sharp = True
        mesh.update()

    changed_by(obj, sharp_edge, "a sharp edge")

    def custom_normals():
        mesh.normals_split_custom_set([(0.0, 0.0, 1.0)] * len(mesh.loops))
        mesh.update()

    changed_by(obj, custom_normals, "setting custom normals")

    def custom_normals_again():
        mesh.normals_split_custom_set([(1.0, 0.0, 0.0)] * len(mesh.loops))
        mesh.update()

    changed_by(obj, custom_normals_again, "setting other custom normals")

    other = make_cube("PS Surface Weighted")

    def weighted_normal():
        other.modifiers.new("PS Surface Weighted Normal", 'WEIGHTED_NORMAL')

    changed_by(other, weighted_normal, "a Weighted Normal modifier")

    nan_cube = make_cube("PS Surface NaN UV")
    nan_mesh = nan_cube.data
    nan_mesh.uv_layers[UV].uv[0].vector = (float('nan'), 0.0)
    nan_mesh.update()
    update()

    def move_uv_beside_nan():
        nan_mesh.uv_layers[UV].uv[5].vector = (0.9, 0.9)
        nan_mesh.update()

    changed_by(nan_cube, move_uv_beside_nan, "moving one UV of a map that holds a NaN")

    def remove_nan():
        nan_mesh.uv_layers[UV].uv[0].vector = (0.1, 0.1)
        nan_mesh.update()

    changed_by(nan_cube, remove_nan, "replacing a NaN UV")
    before = key(nan_cube)
    nan_mesh.uv_layers[UV].uv[0].vector = (float('nan'), 0.0)
    nan_mesh.update()
    update()
    surface.mark_suspect(nan_cube.session_uid)
    with_nan = key(nan_cube)
    nan_cube.update_tag(refresh={'DATA'})
    update()
    surface.mark_suspect(nan_cube.session_uid)
    check(with_nan != before and key(nan_cube) == with_nan, "a NaN in the same place in both reads keeps the key")


def test_no_surface():
    section("no surface gives None, and the entry still counts as resolved")
    surface.forget()
    obj = make_cube("PS Surface None")
    check(key(obj, "PS Surface Missing") is None, "a missing UV map gives None")
    curve = bpy.data.curves.new("PS Surface Curve", 'CURVE')
    curve_obj = bpy.data.objects.new("PS Surface Curve", curve)
    bpy.context.scene.collection.objects.link(curve_obj)
    update()
    check(key(curve_obj) is None, "a curve object gives None")
    check(surface.peek_key(curve_obj, UV, bpy.context.evaluated_depsgraph_get()) == (None, True),
          "and peeks as resolved and fresh")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        check(key(obj) is None, "an object in Edit Mode gives None")
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
    surface.mark_suspect(obj.session_uid)
    check(key(obj) is not None, "leaving Edit Mode gives a key again")

    twin = bpy.data.objects.new("PS Surface Twin", obj.data)
    bpy.context.scene.collection.objects.link(twin)
    update()
    bpy.context.view_layer.objects.active = twin
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        update()
        surface.mark_suspect(obj.session_uid)
        try:
            twin_key = key(obj)
        except KeyError as error:
            twin_key = error
        check(obj.mode == 'OBJECT' and twin_key is None,
              f"a mesh in Edit Mode through a linked duplicate gives None ({twin_key!r})")
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(twin)
    surface.mark_suspect(obj.session_uid)
    check(key(obj) is not None, "and a key again once the duplicate leaves Edit Mode")

    sphere = make_cube("PS Surface Remesh")
    sphere.modifiers.new("PS Surface Remesh", 'REMESH')
    update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    check(surface.peek_key(sphere, UV, depsgraph) == (None, False), "an object never resolved peeks as unknown")
    check(key(sphere) is None, "a Remesh modifier that drops the UV map gives None")
    check(surface.peek_key(sphere, UV, depsgraph) == (None, True),
          "which peeks as a fresh None, so a draw does not ask again")
    surface.mark_suspect(sphere.session_uid)
    check(surface.peek_key(sphere, UV, depsgraph) == (None, False), "until the object is marked suspect")
    bpy.data.objects.remove(curve_obj)
    bpy.data.curves.remove(curve)


def test_peek_and_request():
    section("peek never reads, request resolves on the tick")
    surface.forget()
    cancel_timers()
    first = make_cube("PS Surface Peek A")
    second = make_cube("PS Surface Peek B", location=(4.0, 0.0, 0.0))
    update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    check(surface.peek_key(first, UV, depsgraph) == (None, False), "before a resolve: (None, False)")
    first_key, second_key = key(first), key(second)
    check(surface.peek_key(first, UV, depsgraph) == (first_key, True), "after a resolve: the key, fresh")
    surface.mark_suspect(first.session_uid)
    check(surface.peek_key(first, UV, depsgraph) == (first_key, False), "a suspect object peeks as not fresh")
    check(surface.peek_key(second, UV, depsgraph) == (second_key, True), "another object stays fresh")

    surface.request(first, UV)
    check(bpy.app.timers.is_registered(surface._tick), "a request schedules the tick")
    cancel_timers_keep_requests()
    changes.clear()
    surface._tick()
    check(surface.peek_key(first, UV, depsgraph) == (first_key, True), "the tick resolves the request")
    check(not changes, f"a key the tick finds unchanged tags no redraw ({len(changes)} calls)")

    notified = []
    notify = session.notify
    session.notify = lambda force=False: notified.append(force)
    try:
        first.data.vertices[0].co.z += 0.5
        first.data.update()
        update()
        check(surface.peek_key(first, UV, bpy.context.evaluated_depsgraph_get())[1] is False,
              "the depsgraph handler marks an edited object suspect")
        check(surface.peek_key(second, UV, bpy.context.evaluated_depsgraph_get()) == (second_key, True),
              "and only that object")
        cancel_timers()
        notified.clear()
        surface.request(first, UV)
        cancel_timers_keep_requests()
        surface._tick()
        new_key = surface.peek_key(first, UV, bpy.context.evaluated_depsgraph_get())
        check(new_key[1] and new_key[0] not in (None, first_key),
              "the tick resolves the new key")
        check(len(changes) == 1 and len(notified) == 1,
              f"a changed key redraws and notifies the session ({len(changes)} redraws, {len(notified)} notifies)")
    finally:
        session.notify = notify
        cancel_timers()

    surface.forget()
    check(not surface._entries and not surface._requests, "forget drops every entry and request")


def cancel_timers_keep_requests():
    """Unregister the tick without dropping its requests, so the test can run it."""
    if bpy.app.timers.is_registered(surface._tick):
        bpy.app.timers.unregister(surface._tick)


def test_entry_limit():
    section("stored arrays and entries are capped, least recently resolved first")
    surface.forget()
    cancel_timers()
    cubes = [make_cube(f"PS Surface Limit {index}", location=(3.0 * index, 0.0, 0.0))
             for index in range(surface.ENTRY_LIMIT + 1)]
    update()
    keys = [key(cube) for cube in cubes]
    with_arrays = [entry(cube).arrays is not None for cube in cubes]
    check(with_arrays == [False] + [True] * surface.ENTRY_LIMIT,
          f"{surface.ENTRY_LIMIT + 1} resolves keep the arrays of {surface.ENTRY_LIMIT}, not the oldest's "
          f"({with_arrays})")
    key(cubes[1])
    key(cubes[0])
    check(entry(cubes[1]).arrays is not None and entry(cubes[2]).arrays is not None,
          "resolving an entry again keeps its arrays")

    # A draw peeks at every object, and the tick resolves what is not
    # fresh. Losing arrays must not look like a change, or each tick would
    # redraw, and the next draw would ask again.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    requested = []
    for _ in range(3):
        peeks = [surface.peek_key(cube, UV, depsgraph) for cube in cubes]
        stale = [cube for cube, (_, fresh) in zip(cubes, peeks) if not fresh]
        for cube in stale:
            surface.request(cube, UV)
        cancel_timers_keep_requests()
        changes.clear()
        surface._tick()
        requested.append((len(stale), len(changes)))
    check(requested == [(0, 0)] * 3 and [peek[0] for peek in peeks] == keys,
          f"more objects than ENTRY_LIMIT all peek fresh with their keys (requests and redraws {requested})")
    surface.mark_suspect(cubes[0].session_uid)
    changes.clear()
    surface.request(cubes[0], UV)
    cancel_timers_keep_requests()
    surface._tick()
    check(entry(cubes[0]).arrays is not None and key(cubes[0]) == keys[0] and not changes,
          f"an entry without arrays reads them again with the same key and no redraw ({len(changes)} redraws)")

    surface.forget()
    obj = cubes[0]
    names = [f"PS Surface Map {index}" for index in range(surface.KEY_LIMIT + 1)]
    for name in names:
        surface.resolve_key(obj, name)
    kept = [name for _, name, _ in surface._entries]
    check(len(surface._entries) == surface.KEY_LIMIT and kept == names[1:],
          f"{surface.KEY_LIMIT + 1} resolves keep {surface.KEY_LIMIT} entries without the oldest ({len(kept)})")
    for cube in cubes:
        bpy.data.objects.remove(cube)
    surface.forget()


def test_view_layers():
    section("each view layer resolves and peeks on its own depsgraph")
    surface.forget()
    cancel_timers()
    obj = make_cube("PS Surface Layers")
    obj.modifiers.new("PS Surface Layers Subsurf", 'SUBSURF').levels = 1
    scene = bpy.context.scene
    second = scene.view_layers.new("PS Surface Second")
    try:
        update()
        second.update()
        first_depsgraph = bpy.context.evaluated_depsgraph_get()
        second_depsgraph = second.depsgraph
        first_key = key(obj)
        check(surface.peek_key(obj, UV, second_depsgraph) == (None, False),
              "a key resolved on one view layer is unknown on another")
        surface.request(obj, UV, second_depsgraph)
        cancel_timers_keep_requests()
        surface._tick()
        second_peek = surface.peek_key(obj, UV, second_depsgraph)
        check(second_peek == (first_key, True),
              f"a request from the other view layer's draw resolves on its depsgraph (fresh {second_peek[1]})")
        check(surface.peek_key(obj, UV, first_depsgraph) == (first_key, True),
              "and the first view layer stays fresh")
    finally:
        scene.view_layers.remove(second)
        bpy.data.objects.remove(obj)
        surface.forget()


def test_handlers():
    section("the handlers mark, notify and forget")
    surface.forget()
    cancel_timers()
    obj = make_cube("PS Surface Handlers A")
    other = make_cube("PS Surface Handlers B", location=(4.0, 0.0, 0.0))
    update()
    key(obj)
    key(other)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    check((handlers.on_frame_change_post in bpy.app.handlers.frame_change_post)
          and (handlers.on_undo_post in bpy.app.handlers.undo_post),
          "the frame change and undo handlers are registered")

    notified = []
    notify = session.notify
    session.notify = lambda force=False: notified.append(force)
    try:
        scene = bpy.context.scene
        scene.frame_set(scene.frame_current + 1)
        check(not surface.peek_key(obj, UV, depsgraph)[1] and not surface.peek_key(other, UV, depsgraph)[1],
              "a frame change marks every surface suspect")
        check(notified, f"and notifies the session ({len(notified)} calls)")
        scene.frame_set(scene.frame_current - 1)

        # A render job calls the handler from its own thread with the
        # render depsgraph; it must touch neither the entries nor timers.
        key(obj)
        notified.clear()
        handlers.on_frame_change_post(scene, types.SimpleNamespace(mode='RENDER'))
        check(surface.peek_key(obj, UV, depsgraph)[1] and not notified,
              "a frame change on a render depsgraph marks nothing and does not notify")
    finally:
        session.notify = notify
        cancel_timers()

    key(obj)
    key(other)
    handlers.on_undo_post()
    cancel_timers()
    check(not surface.peek_key(obj, UV, depsgraph)[1] and not surface.peek_key(other, UV, depsgraph)[1],
          "an undo marks every surface suspect")
    handlers.on_load_post()
    cancel_timers()
    check(not surface._entries, "a file load forgets every entry")


def test_resolve_uv_map():
    section("resolve_uv_map names the map a layer paints through")
    obj = make_cube("PS Surface UV Maps")
    second = obj.data.uv_layers.new(name="PS Surface Second")
    check(texel_map.resolve_uv_map(obj, "") == UV, "'' is the active render UV map")
    second.active_render = True
    check(texel_map.resolve_uv_map(obj, "") == "PS Surface Second", "which follows active_render")
    check(texel_map.resolve_uv_map(obj, UV) == UV, "a name that exists is kept")
    check(texel_map.resolve_uv_map(obj, "PS Surface Missing") is None, "a missing name gives None")
    empty = bpy.data.objects.new("PS Surface Empty", None)
    check(texel_map.resolve_uv_map(empty, "") is None, "an object without a mesh gives None")
    bpy.data.objects.remove(empty)


guarded(test_stable_without_content_change)
guarded(test_content_changes)
guarded(test_no_surface)
guarded(test_peek_and_request)
guarded(test_entry_limit)
guarded(test_view_layers)
guarded(test_handlers)
guarded(test_resolve_uv_map)
surface._surfaces_changed = _surfaces_changed
cancel_timers()
surface.release()
texel_map.release()
finish("SURFACE TEST")
