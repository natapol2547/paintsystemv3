"""The selection clips native strokes in a real window (PS-091, slice 3).

The headless test calls `stencil.sync` itself. This one lets the window
loop run the session's tick and the message bus, paints a native
projection stroke through the stencil and checks that the painted row
follows the feathered mask, that user edits of the stencil are set back,
that leaving and entering texture paint mode restores and re-applies, and
that undo and redo keep the stencil in step with the selection.

Run:  blender --factory-startup --python tests/test_selection_stencil_ui.py
"""
import os
import sys
import traceback

import bpy
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Quaternion

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import before, check, finish, import_from, register_addon, section, since, skip  # noqa: E402

if bpy.app.background:
    print("test_selection_stencil_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
session = import_from("selection.session")
raster = import_from("selection.raster")
stencil = import_from("selection.stencil")

USER_STENCIL = "PS Stencil UI User"


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return window, area, region


def plane():
    return bpy.data.objects["PS Stencil Plane"]


def tree():
    return plane().active_material.paint_system.tree


def layer_image():
    return tree().nodes.active.image


def image_paint():
    return bpy.context.scene.tool_settings.image_paint


def override():
    window, area, region = view3d()
    return bpy.context.temp_override(window=window, area=area, region=region,
                                     object=plane(), active_object=plane())


def make_plane():
    mesh = bpy.data.meshes.new("PS Stencil Plane")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, co in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = co
    mesh.uv_layers.new(name="UV2")
    obj = bpy.data.objects.new("PS Stencil Plane", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def setup_brush():
    """Full strength, constant falloff, mix blend: a painted texel shows the stencil's value."""
    settings = bpy.context.scene.tool_settings
    brush = settings.image_paint.brush
    brush.strength = 1.0
    brush.use_pressure_strength = False
    for name in ("curve_distance_falloff_preset", "curve_preset"):
        if hasattr(brush, name):
            setattr(brush, name, 'CONSTANT')
            break
    brush.color = (1.0, 0.0, 0.0)
    brush.blend = 'MIX'
    brush.size = 30
    for owner in (settings.image_paint, settings):
        unified = getattr(owner, "unified_paint_settings", None)
        if unified is not None:
            unified.color = (1.0, 0.0, 0.0)
            unified.size = 30
            unified.strength = 1.0


def stroke_elements(points):
    keys = {prop.identifier for prop in bpy.types.OperatorStrokeElement.bl_rna.properties}
    elements = []
    for index, (x, y) in enumerate(points):
        element = dict(name="", location=(0, 0, 0), mouse=(x, y), mouse_event=(x, y), pressure=1.0, size=30,
                       pen_flip=False, time=index * 0.01, is_start=index == 0, x_tilt=0.0, y_tilt=0.0)
        elements.append({key: value for key, value in element.items() if key in keys})
    return elements


def pixels(image):
    width, height = image.size
    values = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(height, width, 4)


def fill_white(image):
    width, height = image.size
    image.pixels.foreach_set(np.ones(width * height * 4, np.float32))
    image.update()


def user_stencil_back():
    settings = image_paint()
    return (not settings.use_stencil_layer and not settings.invert_stencil
            and settings.stencil_image is not None and settings.stencil_image.name == USER_STENCIL
            and plane().data.uv_layer_stencil_index == 1)


def applied():
    state = session.current()
    image = stencil.stencil_image()
    return (state.active and stencil.is_applied(bpy.context.scene) and image is not None
            and os.path.basename(image.filepath) == state.digest.hex() + ".png")


def stencil_matches_mask():
    state = session.current()
    expected = raster.get_mask(tree().selection, state.size, state.tile).read_bytes()
    got = np.rint(pixels(stencil.stencil_image())[..., 0] * 255).astype(np.uint8)
    return got.shape == expected.shape and np.array_equal(got, expected)


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass."""
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def paint_across():
    """A stroke along the middle row of the plane; returns the painted amount and the mask on that row."""
    image = layer_image()
    fill_white(image)
    _, _, region = view3d()
    rv3d = region.data
    points = [tuple(location_3d_to_region_2d(region, rv3d, (2 * (0.01 + 0.98 * i / 119) - 1, 0.0, 0.0)))
              for i in range(120)]
    with override():
        result = bpy.ops.paint.image_paint(stroke=stroke_elements(points), mode='NORMAL')
    width, height = image.size
    painted = 1.0 - pixels(image)[height // 2, :, 1]
    state = session.current()
    mask = raster.get_mask(tree().selection, state.size, state.tile).read()[height // 2]
    return result, painted, mask


def steps():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    obj = make_plane()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with override():
        bpy.ops.paint_system.setup_material()
        bpy.ops.paint_system.add_layer(layer_type='IMAGE', resolution='1024')
    _, area, region = view3d()
    rv3d = region.data
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Quaternion()
    rv3d.view_location = (0, 0, 0)
    rv3d.view_distance = 3.0
    area.spaces.active.shading.type = 'MATERIAL'
    settings = image_paint()
    settings.stencil_image = bpy.data.images.new(USER_STENCIL, 8, 8)
    settings.invert_stencil = False
    settings.use_stencil_layer = False
    obj.data.uv_layer_stencil_index = 1
    with override():
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    setup_brush()
    yield 0.5

    section("a native stroke paints through the feathered mask")
    check(user_stencil_back(), "without a selection the user's stencil is untouched")
    tree().selection.add_op('BOX', points=[(0.3, -0.1), (0.7, 1.1)], feather=128.0)
    session.notify()
    yield from wait_for(applied)
    check(applied(), "the tick applied the selection's mask file")
    check(stencil_matches_mask(), "the stencil image holds the mask's bytes")
    result, painted, mask = paint_across()
    width = len(mask)
    columns = np.arange(width)
    interior = (columns > 20) & (columns < width - 20)
    if before(4, 3):
        # 4.2 projection painting misses the texel on the quad's diagonal
        # with or without a stencil.
        interior[width // 2] = False
    difference = np.abs(painted[interior] - mask[interior])
    check(result == {'FINISHED'} and difference.max() <= 0.003,
          f"the painted row follows the mask ({result}, max difference {difference.max():.4f})")
    check(painted[interior].sum() > 1.0 and (painted[interior & (mask == 0)] == 0).all(),
          "paint lands inside and nothing lands outside")

    section("a selection whose stencil cannot be written blocks strokes")
    block_file = os.path.join(stencil._file_dir(), stencil.BLOCK_FILE)
    if os.path.exists(block_file):
        os.remove(block_file)
    write_png = stencil._write_png

    def unwritable(path, grey):
        raise OSError("No space left on device")

    def generated_block_applied():
        image = image_paint().stencil_image
        return image is not None and bool(image.get(stencil.BLOCK_KEY)) and image_paint().invert_stencil

    stencil._write_png = unwritable
    try:
        tree().selection.add_op('BOX', points=[(0.2, -0.1), (0.8, 1.1)], feather=64.0)
        session.notify()
        yield from wait_for(generated_block_applied)
        check(generated_block_applied(), "with no file at all, the generated block image holds the stencil")
        result, painted, _ = paint_across()
        check(result == {'FINISHED'} and painted.max() <= 0.003,
              f"and a stroke paints nothing ({result}, max painted {painted.max():.4f})")
    finally:
        stencil._write_png = write_png
    tree().selection.add_op('BOX', points=[(0.3, -0.1), (0.7, 1.1)], feather=128.0)
    session.notify()
    yield from wait_for(applied)
    check(applied(), "the earlier selection applies its mask file again")

    section("user edits of the stencil are set back")
    image_paint().invert_stencil = False
    yield from wait_for(lambda: image_paint().invert_stencil)
    check(image_paint().invert_stencil, "turning Invert off is undone by the next tick")
    plane().data.uv_layer_stencil_index = 1
    yield from wait_for(lambda: plane().data.uv_layer_stencil_index == 0)
    check(plane().data.uv_layer_stencil_index == 0, "so is picking another stencil UV map")

    section("leaving texture paint mode gives the stencil back")
    with override():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield from wait_for(user_stencil_back)
    check(user_stencil_back() and not stencil.is_applied(bpy.context.scene), "object mode has the user's stencil")
    with override():
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    yield from wait_for(applied)
    check(applied(), "entering texture paint mode applies the selection again")

    section("undo and redo")
    tree().selection.clear()
    session.notify()
    yield from wait_for(user_stencil_back)
    with override():
        bpy.ops.ed.undo_push(message="PS stencil test: no selection")
        check(bpy.ops.paint_system.select_all('EXEC_DEFAULT', True, action='SELECT') == {'FINISHED'},
              "select all runs")
    yield from wait_for(applied)
    check(applied(), "the selection applies")
    if since(5, 1):
        with override():
            bpy.ops.ed.undo()
        yield from wait_for(user_stencil_back)
        check(tree().selection.is_empty and user_stencil_back(),
              "undoing past the selection's creation gives the user's stencil back")
        with override():
            bpy.ops.ed.redo()
        yield from wait_for(applied)
        check(applied() and stencil_matches_mask(), "redo applies it again with the mask's pixels")
    else:
        # Before 5.1, undo in texture paint mode steps through image undo
        # only (undo.UNDO_OPTIONS), so the selection stays.
        with override():
            bpy.ops.ed.undo()
        yield 0.5
        yield from wait_for(lambda: applied() == (not tree().selection.is_empty))
        check(applied() == (not tree().selection.is_empty) and (not applied() or stencil_matches_mask()),
              "after undo the stencil still matches the selection")
        skip("before 5.1, undo in texture paint mode does not restore the ops")

    tree().selection.clear()
    session.notify()
    yield from wait_for(user_stencil_back)
    check(user_stencil_back(), "clearing the selection ends with the user's stencil")
    session.release()
    stencil.unregister()
    raster.release()


def driver_for(gen):
    def driver():
        try:
            delay = next(gen)
        except StopIteration:
            finish("SELECTION STENCIL UI TEST")
        except Exception:
            traceback.print_exc()
            check(False, "exception in the steps")
            finish("SELECTION STENCIL UI TEST")
        return 0.1 if delay is None else delay
    return driver


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
