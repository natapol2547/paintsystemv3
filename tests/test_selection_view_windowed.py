"""Selections drawn in a real 3D view, recorded from its region (PS-093).

The headless test builds `VIEW` masks from matrices it makes up. This one
records each op as a tool does, from a `SpaceView3D` region's
`width`/`height`, `view_matrix @ matrix_world` and `window_matrix`, in a
single view and in quad view with region overlap on and off, and checks
the selected texels against `location_3d_to_region_2d`. The cube is moved,
rotated and scaled first, so the stored object-to-view matrix matters.
Then it lets the window loop run the session: a native stroke across the
box is clipped by the stencil, the overlay draws ants for the selection,
and a box over empty background counts as no selection.

Run:  blender --factory-startup --python tests/test_selection_view_windowed.py
"""
import os
import sys
import traceback

import bpy
import gpu
import numpy as np
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

if bpy.app.background:
    print("test_selection_view_windowed.py needs a window; run without -b")
    sys.exit(2)

register_addon()
texel_map = import_from("gpu_passes.texel_map")
session = import_from("selection.session")
raster = import_from("selection.raster")
view_raster = import_from("selection.view_raster")
stencil = import_from("selection.stencil")
overlay = import_from("selection.overlay")

SIZE = 256
PLACED = Matrix.LocRotScale((0.4, -0.3, 0.2), Euler((0.0, 0.0, 0.3)), (1.3, 0.9, 1.1))
RED_GREEN_ANTS = {
    "selection_ant_color_a": (1.0, 0.0, 0.0),
    "selection_ant_color_b": (0.0, 1.0, 0.0),
    "selection_wash_opacity": 0.0,
}
"""Overlay settings for the ant counts: dash colours the Stencil Mask display cannot produce, and no wash."""

captures = {}
wanted = set()


def _capture():
    """Read the region back when a step asked for this area; runs after the overlay's own handler."""
    area = bpy.context.area
    if area is None or area.as_pointer() not in wanted:
        return
    region = bpy.context.region
    width, height = region.width, region.height
    buffer = gpu.types.Buffer('UBYTE', width * height * 4)
    gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE', data=buffer)
    captures[area.as_pointer()] = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4).astype(np.int32)
    wanted.discard(area.as_pointer())


capture_handle = bpy.types.SpaceView3D.draw_handler_add(_capture, (), 'WINDOW', 'POST_PIXEL')


def red_green_ants(pixels):
    """Opaque pure red or pure green pixels, the dashes drawn with `RED_GREEN_ANTS`."""
    high = pixels[..., :3] > 225
    low = pixels[..., :3] < 30
    red = high[..., 0] & low[..., 1] & low[..., 2]
    green = low[..., 0] & high[..., 1] & low[..., 2]
    return (red | green) & (pixels[..., 3] > 250)


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def window_regions():
    return [r for r in view3d().regions if r.type == 'WINDOW']


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def layer_image():
    return tree().nodes.active.image


def image_paint():
    return bpy.context.scene.tool_settings.image_paint


def override():
    return bpy.context.temp_override(window=window(), area=view3d(), region=window_regions()[-1],
                                     object=cube(), active_object=cube())


def flat(matrix):
    """A rows-first matrix as a `subtype='MATRIX'` property takes it, column by column."""
    return np.asarray(matrix, dtype=np.float64).T.ravel().tolist()


def add_view_box(region, lo, hi):
    """Replace the selection with a hard `VIEW` box in *region*'s pixels, recorded as a tool records it."""
    rv3d = region.data
    stored = np.array(rv3d.view_matrix, dtype=np.float64) @ np.array(cube().matrix_world, dtype=np.float64)
    op = tree().selection.add_op('BOX', 'REPLACE', space='VIEW', points=[tuple(lo), tuple(hi)],
                                 region_size=(region.width, region.height), view_matrix=flat(stored),
                                 projection_matrix=flat(rv3d.window_matrix), object=cube(), uv_map="UVMap",
                                 through=False)
    op.feather = 0.0
    op.antialias = False
    return op


def region_point(region, point):
    return np.array(location_3d_to_region_2d(region, region.data, Vector(point)), dtype=np.float64)


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


def painted_amount():
    """How much of the brush colour each layer texel holds, `(height, width)`, from a white start."""
    image = layer_image()
    width, height = image.size
    values = np.empty(width * height * 4, np.float32)
    image.pixels.foreach_get(values)
    return 1.0 - values.reshape(height, width, 4)[..., 1]


def paint_across_top():
    """Fill the layer white and paint a stroke across the cube's top face along its local x axis."""
    image = layer_image()
    width, height = image.size
    image.pixels.foreach_set(np.ones(width * height * 4, np.float32))
    image.update()
    region = window_regions()[-1]
    points = [tuple(region_point(region, PLACED @ Vector((-0.95 + 1.9 * i / 119, 0.0, 1.0)))) for i in range(120)]
    with override():
        result = bpy.ops.paint.image_paint(stroke=stroke_elements(points), mode='NORMAL')
    return result, painted_amount()


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass."""
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def settle():
    """Yield until the session is idle and the view has drawn the state it synced."""
    yield from wait_for(lambda: not bpy.app.timers.is_registered(session._tick))
    view3d().tag_redraw()
    yield 0.3


def capture():
    """Yield until the 3D view has drawn once more, then return its pixels (or None)."""
    key = view3d().as_pointer()
    captures.pop(key, None)
    wanted.add(key)
    view3d().tag_redraw()
    yield from wait_for(lambda: key in captures)
    wanted.discard(key)
    return captures.get(key)


def applied():
    state = session.current()
    image = stencil.stencil_image()
    return (state.active and stencil.is_applied(bpy.context.scene) and image is not None
            and os.path.basename(image.filepath) == state.digest.hex() + ".png")


def set_layout(quad, overlap, scale):
    preferences = bpy.context.preferences
    preferences.system.use_region_overlap = overlap
    preferences.view.ui_scale = scale
    view3d().spaces.active.show_region_ui = True
    if quad != (len(window_regions()) > 1):
        with bpy.context.temp_override(window=window(), area=view3d(), region=window_regions()[0]):
            bpy.ops.screen.region_quadview()


def near_edges(region, screen, reach=2.0):
    """Whether each screen point lies within *reach* pixels of a projected edge of the cube."""
    corners = [region_point(region, cube().matrix_world @ vertex.co) for vertex in cube().data.vertices]
    near = np.zeros(len(screen), dtype=bool)
    for edge in cube().data.edges:
        start, end = corners[edge.vertices[0]], corners[edge.vertices[1]]
        step = end - start
        t = np.clip((screen - start) @ step / max(float(step @ step), 1e-12), 0.0, 1.0)
        near |= np.linalg.norm(screen - (start + t[:, None] * step), axis=1) <= reach
    return near


def check_region(label, index, region):
    """Record a box round the cube in *region* and compare the mask with where the texels project."""
    rv3d = region.data
    width, height = region.width, region.height
    centre = region_point(region, PLACED.translation)
    lo = centre - (0.061 * width, 0.052 * height)
    hi = centre + (0.043 * width, 0.071 * height)
    add_view_box(region, lo, hi)
    mask = raster.get_mask(tree().selection, (SIZE, SIZE)).read().ravel()
    found = texel_map.get_texel_map(cube(), "UVMap", (SIZE, SIZE), fallback_to_active=False)
    positions = found.positions().reshape(-1, 4).astype(np.float64)
    normals = found.normals().reshape(-1, 4)[:, :3].astype(np.float64)
    view = np.array(rv3d.view_matrix, dtype=np.float64)
    clip = np.c_[positions[:, :3], np.ones(len(positions))] @ np.array(rv3d.perspective_matrix, dtype=np.float64).T
    screen = (clip[:, :2] / clip[:, 3:4] * 0.5 + 0.5) * (width, height)
    if rv3d.is_perspective:
        to_eye = np.linalg.inv(view)[:3, 3] - positions[:, :3]
    else:
        to_eye = np.broadcast_to(view[2, :3], normals.shape)
    # Faces seen edge-on project onto the outline and may go either way.
    facing = np.einsum('ij,ij->i', normals, to_eye) / np.linalg.norm(to_eye, axis=1)
    front = facing > 0.1
    island = positions[:, 3] > view_raster.MARGIN_ALPHA
    selected = island & (mask > 0.5)
    outside = selected & ~((screen >= lo - 1.0) & (screen <= hi + 1.0)).all(axis=1)
    inside = island & front & ((screen >= lo + 1.0) & (screen <= hi - 1.0)).all(axis=1)
    # Within a pixel or two of an edge the depth filter reads the face or
    # background beyond it, which hides a steep face there (`TEXEL_SLOPE_CAP`).
    at_edge = inside & near_edges(region, screen)
    missed = inside & ~at_edge & ~selected
    rng = np.random.default_rng(index)
    picks = np.flatnonzero(selected)
    picks = rng.choice(picks, min(200, len(picks)), replace=False) if len(picks) else picks
    api_outside = 0
    for pick in picks:
        point = location_3d_to_region_2d(region, rv3d, Vector(positions[pick, :3]))
        if point is None or not ((lo - 1.0 <= np.array(point)) & (np.array(point) <= hi + 1.0)).all():
            api_outside += 1
    check(inside.sum() > 50 and not outside.any() and not missed.any() and api_outside == 0,
          f"{label}, region {index} ({width}x{height}, {'perspective' if rv3d.is_perspective else 'orthographic'}): "
          f"{int(selected.sum())} texels selected, {int(outside.sum())} project more than 1 px outside the box "
          f"({api_outside} of {len(picks)} by location_3d_to_region_2d), {int(missed.sum())} of "
          f"{int(inside.sum())} facing texels inside it missed, {int(at_edge.sum())} within 2 px of an edge not counted")


def steps():
    obj = cube()
    obj.matrix_world = PLACED
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    with override():
        bpy.ops.paint_system.setup_material('EXEC_DEFAULT')
        bpy.ops.paint_system.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    tree().nodes.active.image = bpy.data.images.new("PS View Windowed Layer", SIZE, SIZE)
    view3d().spaces.active.shading.type = 'MATERIAL'
    with override():
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)
    preferences = bpy.context.preferences
    defaults = (preferences.system.use_region_overlap, preferences.view.ui_scale)

    section("a box recorded from a region selects the texels that project inside it")
    layouts = (("single view, region overlap on", False, True, 1.0),
               ("single view, region overlap off", False, False, 1.0),
               ("quad view, region overlap on", True, True, 1.0),
               ("quad view, region overlap off, UI scale 2", True, False, 2.0))
    try:
        for label, quad, overlap, scale in layouts:
            set_layout(quad, overlap, scale)
            view3d().tag_redraw()
            yield 0.8
            regions = window_regions()
            check(len(regions) == (4 if quad else 1), f"{label}: {len(regions)} window regions")
            for index, region in enumerate(regions):
                check_region(label, index, region)
    finally:
        set_layout(False, *defaults)
    tree().selection.clear()
    session.notify()
    view3d().tag_redraw()
    yield 0.8

    region = window_regions()[-1]
    rv3d = region.data
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Quaternion()
    rv3d.view_location = PLACED @ Vector((0.0, 0.0, 1.0))
    rv3d.view_distance = 5.0
    setup_brush()
    yield from settle()

    section("a native stroke across the box is clipped by the stencil")
    result, unclipped = paint_across_top()
    left = region_point(region, PLACED @ Vector((-0.3, 0.0, 1.0)))
    right = region_point(region, PLACED @ Vector((0.3, 0.0, 1.0)))
    add_view_box(region, (min(left[0], right[0]), left[1] - 120.0), (max(left[0], right[0]), left[1] + 120.0))
    session.notify()
    yield from wait_for(applied)
    check(result == {'FINISHED'} and applied(), f"the stroke ran and the selection's mask file is applied ({result})")
    result, painted = paint_across_top()
    state = session.current()
    mask = raster.get_mask(tree().selection, state.size, state.tile).read()
    footprint = unclipped > 0.999
    # Texels on the box's hard edge may be off by one 8-bit step: the
    # stencil is read filtered.
    tolerance = 1.5 / 255
    difference = np.abs(painted[footprint] - mask[footprint])
    check(result == {'FINISHED'} and difference.max() <= tolerance,
          f"under the stroke the paint follows the mask ({result}, max difference {difference.max():.4f})")
    outside = painted[mask == 0.0]
    inside_count, outside_count = int((footprint & (mask > 0.999)).sum()), int((footprint & (mask == 0.0)).sum())
    check(inside_count > 20 and outside_count > 20 and outside.max() <= tolerance,
          f"the stroke crosses the box's edges, and nothing lands outside it ({inside_count} texels in, "
          f"{outside_count} out, at most {outside.max():.4f} painted outside)")

    section("the overlay draws ants for a VIEW selection")
    default_style = {name: overlay.DEFAULTS[name] for name in RED_GREEN_ANTS}
    overlay.DEFAULTS.update(RED_GREEN_ANTS)
    try:
        tree().selection.clear()
        session.notify()
        yield from wait_for(lambda: not session.current().selected)
        yield from settle()
        pixels = yield from capture()
        baseline = int(red_green_ants(pixels).sum()) if pixels is not None else -1
        centre = region_point(region, PLACED @ Vector((0.0, 0.0, 1.0)))
        add_view_box(region, centre - (60.3, 70.2), centre + (40.7, 50.6))
        session.notify()
        yield from wait_for(lambda: session.current().active)
        yield from settle()
        pixels = yield from capture()
        shown = int(red_green_ants(pixels).sum()) if pixels is not None else -1
        check(baseline >= 0 and shown > baseline + 200,
              f"the ants show on the surface ({shown} ant pixels, {baseline} without a selection)")

        section("a box over empty background is no selection")
        add_view_box(region, (5.2, 5.4), (40.6, 40.3))
        session.notify()
        yield from wait_for(lambda: session.current().empty)
        yield from settle()
        state = session.current()
        check(state.selected and state.empty and not state.active and not state.reason
              and session.label(state) == session.NOTHING_SELECTED and len(tree().selection.ops) == 1,
              f"the op stays and the session says {session.label(state)!r}")
        check(not stencil.is_applied(bpy.context.scene), "the stencil is not applied")
        pixels = yield from capture()
        empty = int(red_green_ants(pixels).sum()) if pixels is not None else -1
        check(0 <= empty <= baseline + 20, f"no ants draw ({empty} ant pixels, {baseline} without a selection)")
        result, painted = paint_across_top()
        check(result == {'FINISHED'} and np.array_equal(painted > 0.999, unclipped > 0.999),
              f"a stroke paints unclipped ({int((painted > 0.999).sum())} texels, {int(footprint.sum())} before)")
    finally:
        overlay.DEFAULTS.update(default_style)
    tree().selection.clear()
    session.notify()
    yield from wait_for(lambda: not session.current().selected)


def driver_for(gen):
    def driver():
        try:
            delay = next(gen)
        except StopIteration:
            end()
        except Exception:
            traceback.print_exc()
            check(False, "exception in the steps")
            end()
        return 0.1 if delay is None else delay
    return driver


def end():
    bpy.types.SpaceView3D.draw_handler_remove(capture_handle, 'WINDOW')
    session.release()
    stencil.unregister()
    raster.release()
    finish("SELECTION VIEW WINDOWED TEST")


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
