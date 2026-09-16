"""The selection overlay in a real window: draws, pixels, the redraw timer and the safety net (PS-091).

The headless test draws the shaders into offscreen targets. This one lets
the window loop run the draw handlers and the redraw timer, and reads the
regions back from a draw handler added after the overlay's, so a pop-up
over the window (the splash screen) does not get in the way. What that
handler reads is the region's overlay layer, without the scene or the
image under it; opaque pixels that are pure black or white, the default
dash colours, are counted as ants.

Draws are counted by wrapping `overlay._uniforms`, which only a draw that
gets as far as the GPU calls, and batch builds by wrapping
`overlay._mesh_batch`.

Run:  blender --factory-startup --python tests/test_selection_overlay_ui.py
"""
import os
import sys
import time
import traceback

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

if bpy.app.background:
    print("test_selection_overlay_ui.py needs a window; run without -b")
    sys.exit(2)

register_addon()
selection = import_from("selection")
session = import_from("selection.session")
raster = import_from("selection.raster")
overlay = import_from("selection.overlay")

draws = {"view3d": 0, "image": 0}
builds = []
captures = {}
wanted = set()

_uniforms = overlay._uniforms
_mesh_batch = overlay._mesh_batch


def _counting_uniforms(shader, *args, **kwargs):
    draws["view3d" if shader is overlay._shaders.get("screen") else "image"] += 1
    return _uniforms(shader, *args, **kwargs)


def _counting_mesh_batch(*args, **kwargs):
    builds.append(args[1])
    return _mesh_batch(*args, **kwargs)


overlay._uniforms = _counting_uniforms
overlay._mesh_batch = _counting_mesh_batch


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


capture_handles = [
    (bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(_capture, (), 'WINDOW', 'POST_PIXEL')),
    (bpy.types.SpaceImageEditor, bpy.types.SpaceImageEditor.draw_handler_add(_capture, (), 'WINDOW', 'POST_PIXEL')),
]


def ants(pixels):
    """Opaque pure black or white pixels.

    A draw handler reads the viewport's overlay layer, which is transparent
    where only the scene shows, so the alpha keeps an empty pixel from
    counting as black.
    """
    rgb = pixels[..., :3]
    return ((rgb < 30).all(-1) | (rgb > 225).all(-1)) & (pixels[..., 3] > 250)


def window():
    return bpy.context.window_manager.windows[0]


def big_area():
    return max(window().screen.areas, key=lambda a: a.width * a.height)


def side_area():
    return next(a for a in window().screen.areas if a.type in {'PROPERTIES', 'IMAGE_EDITOR'} and a != big_area())


def main_region(area):
    return next(r for r in area.regions if r.type == 'WINDOW')


def override(area):
    return bpy.context.temp_override(window=window(), screen=window().screen, area=area, region=main_region(area))


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def layer():
    return tree().nodes.active


def redraw():
    for area in window().screen.areas:
        area.tag_redraw()


def wait_for(condition, timeout=5.0):
    """Yield to the window loop until *condition()* holds or *timeout* seconds pass."""
    waited = 0.0
    while not condition() and waited < timeout:
        yield 0.05
        waited += 0.05


def capture(area):
    """Yield until *area* has drawn once more, then return its pixels (or None)."""
    key = area.as_pointer()
    captures.pop(key, None)
    wanted.add(key)
    area.tag_redraw()
    yield from wait_for(lambda: key in captures)
    wanted.discard(key)
    return captures.get(key)


def settle():
    """Yield until the session is idle and every area has drawn the state it synced."""
    yield from wait_for(lambda: not bpy.app.timers.is_registered(session._tick))
    redraw()
    yield 0.3


def select_box(u0, v0, u1, v1):
    selection_ops = tree().selection
    selection_ops.clear()
    selection_ops.add_op('BOX', points=[(u0, v0), (u1, v1)], feather=0.0)
    session.notify()


def run_centre(values, expected, reach=6):
    """The mean position of the entries of *values* within *reach* of *expected*, or None."""
    near = [value for value in values if abs(value - expected) <= reach]
    return float(sum(near) / len(near)) if near else None


def steps():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    view3d = big_area()
    with override(view3d):
        bpy.ops.paint_system.setup_material('EXEC_DEFAULT')
        bpy.ops.paint_system.add_layer('EXEC_DEFAULT', layer_type='IMAGE', resolution='1024')
    layer().image = bpy.data.images.new("PS Overlay UI Layer", 256, 256)
    view3d.spaces.active.region_3d.view_location = (0.0, 0.0, 0.0)
    view3d.spaces.active.region_3d.view_distance = 6.0
    with override(view3d):
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
    yield from wait_for(lambda: session.current().paint_mode)
    yield from settle()
    baseline = yield from capture(view3d)
    baseline_ants = int(ants(baseline).sum()) if baseline is not None else -1

    section("the 3D view draws a partial selection and marches the ants")
    select_box(0.3, 0.3, 0.7, 0.7)
    yield from wait_for(lambda: session.current().active)
    draws["view3d"] = 0
    yield from wait_for(lambda: draws["view3d"] > 0)
    check(session.current().active and draws["view3d"] > 0,
          f"the 3D view draws in Texture Paint mode ({draws['view3d']} draws, {session.current().reason})")
    check(overlay.timer_running(), "the redraw timer runs while it shows")
    before = draws["view3d"]
    yield 0.6
    check(draws["view3d"] - before >= 2, f"the timer keeps redrawing the view ({draws['view3d'] - before} draws in 0.6 s)")
    pixels = yield from capture(view3d)
    partial_ants = int(ants(pixels).sum()) if pixels is not None else -1
    check(baseline_ants >= 0 and partial_ants > baseline_ants + 200,
          f"the ants show on the surface ({partial_ants} ant pixels, {baseline_ants} without a selection)")

    section("a selection that ends on UV seams draws no ants along them")
    uvs = np.empty(len(obj.data.uv_layers.active.data) * 2, dtype=np.float32)
    obj.data.uv_layers.active.data.foreach_get('uv', uvs)
    uvs = uvs.reshape(-1, 2)
    size = layer().image.size[0]
    low = np.floor(uvs.min(axis=0) * size) / size
    high = np.ceil(uvs.max(axis=0) * size) / size
    select_box(float(low[0]), float(low[1]), float(high[0]), float(high[1]))
    digest = session.current().digest
    yield from wait_for(lambda: session.current().digest != digest)
    yield from settle()
    pixels = yield from capture(view3d)
    seam_ants = int(ants(pixels).sum()) if pixels is not None else -1
    check(pixels is not None and abs(seam_ants - baseline_ants) <= 20,
          f"the islands' own outline stays hidden ({seam_ants} ant pixels, {baseline_ants} without a selection, "
          f"box {low.tolist()} to {high.tolist()})")

    section("strokes reuse the batch; a modifier rebuilds it")
    select_box(0.3, 0.3, 0.7, 0.7)
    yield from wait_for(lambda: session.current().active and session.current().digest != digest)
    yield from settle()
    builds.clear()
    region = main_region(view3d)
    centre_x, centre_y = region.width // 2, region.height // 2
    names = {p.identifier for p in bpy.types.OperatorStrokeElement.bl_rna.properties if p.identifier != "rna_type"}
    stroke = []
    for i in range(10):
        element = {"name": "", "location": (0.0, 0.0, 0.0), "mouse": (centre_x - 100 + 20 * i, centre_y),
                   "mouse_event": (centre_x - 100 + 20 * i, centre_y), "pen_flip": False, "is_start": i == 0,
                   "pressure": 1.0, "size": 50.0, "time": float(i), "x_tilt": 0.0, "y_tilt": 0.0}
        stroke.append({key: value for key, value in element.items() if key in names})
    with override(view3d):
        for _ in range(3):
            bpy.ops.paint.image_paint(stroke=stroke, mode='NORMAL')
    draws["view3d"] = 0
    yield from wait_for(lambda: draws["view3d"] >= 3)
    check(draws["view3d"] >= 3 and not builds,
          f"three strokes build no batch ({len(builds)} builds over {draws['view3d']} draws)")
    modifier = obj.modifiers.new("PS Overlay UI Subsurf", 'SUBSURF')
    modifier.levels = 1
    yield from wait_for(lambda: builds)
    yield 0.3
    check(len(builds) == 1, f"a modifier change builds it once ({len(builds)} builds)")
    obj.modifiers.remove(modifier)
    yield 0.3

    section("show_selection_3d off hides the 3D view only")
    editor = side_area()
    editor.type = 'IMAGE_EDITOR'
    editor.spaces.active.image = layer().image
    overlay.DEFAULTS["show_selection_3d"] = False
    try:
        redraw()
        yield 0.2
        draws.update(view3d=0, image=0)
        yield from wait_for(lambda: draws["image"] >= 2)
        view3d.tag_redraw()
        yield 0.3
        check(draws["image"] >= 2 and draws["view3d"] == 0,
              f"the image editor still draws and the 3D view does not ({draws})")
        check(overlay.timer_running(), "the timer runs for the image editor alone")
    finally:
        overlay.DEFAULTS["show_selection_3d"] = True

    section("the image editor lines up with the image at zoom 2")
    view3d.type = 'IMAGE_EDITOR'
    space = view3d.spaces.active
    wide = bpy.data.images.new("PS Overlay UI Wide", 512, 256)
    layer().image = wide
    space.image = wide
    select_box(0.25, 0.25, 0.75, 0.75)
    yield from wait_for(lambda: session.current().size == (512, 256) and session.current().active)
    with override(view3d):
        bpy.ops.image.view_zoom_ratio(ratio=2.0)
    view2d = main_region(view3d).view2d

    def box_edges():
        return (view2d.view_to_region(0.25, 0.5, clip=False) + view2d.view_to_region(0.75, 0.5, clip=False)
                + view2d.view_to_region(0.5, 0.25, clip=False) + view2d.view_to_region(0.5, 0.75, clip=False))

    for mode in ('VIEW', 'PAINT'):
        space.mode = mode
        yield from settle()
        # The zoom animates; take pixels drawn with the view that is read.
        edges, pixels = None, None
        for _ in range(10):
            edges = box_edges()
            pixels = yield from capture(view3d)
            if box_edges() == edges:
                break
        if pixels is None:
            check(False, f"{mode}: the image editor drew")
            continue
        x0, y_mid, x1, _, x_mid, y0, _, y1 = edges
        row = [x + 0.5 for x in np.flatnonzero(ants(pixels[y_mid]))]
        column = [y + 0.5 for y in np.flatnonzero(ants(pixels[:, x_mid]))]
        found = (run_centre(row, x0), run_centre(row, x1), run_centre(column, y0), run_centre(column, y1))
        expected = (x0, x1, y0, y1)
        check(all(f is not None and abs(f - e) <= 1.0 for f, e in zip(found, expected)),
              f"{mode}: the outline is within a pixel of the box edges (found {found}, expected {expected}, "
              f"image {x1 - x0} px wide)")

    section("an image resized without an event resyncs from the draw (C15)")
    wide.scale(256, 128)
    yield from wait_for(lambda: session.current().size == (256, 128))
    check(session.current().size == (256, 128) and session.current().active,
          f"a draw noticed the new size and the tick rebuilt ({session.current().size})")

    section("an unrelated image draws nothing and the timer stops")
    view3d.type = 'VIEW_3D'
    with override(view3d):
        bpy.ops.object.mode_set(mode='OBJECT')
    editor.spaces.active.image = bpy.data.images.new("PS Overlay UI Unrelated", 64, 64)
    yield from wait_for(lambda: not session.current().paint_mode)
    yield from wait_for(lambda: not overlay.timer_running(), timeout=2.0)
    draws.update(view3d=0, image=0)
    redraw()
    yield 0.4
    check(not overlay.timer_running(), "the timer stopped")
    check(draws == {"view3d": 0, "image": 0}, f"nothing drew ({draws})")

    section("clearing the selection stops the timer")
    editor.spaces.active.image = wide
    redraw()
    yield from wait_for(overlay.timer_running)
    check(overlay.timer_running(), "the image editor showing the selection starts the timer")
    tree().selection.clear()
    session.notify()
    cleared = time.monotonic()
    yield from wait_for(lambda: not overlay.timer_running(), timeout=2.0)
    elapsed = time.monotonic() - cleared
    check(not overlay.timer_running() and elapsed <= 0.6, f"within 0.6 s ({elapsed:.2f} s)")

    section("unregister leaves nothing behind")
    select_box(0.25, 0.25, 0.75, 0.75)
    yield from wait_for(overlay.timer_running)
    selection.unregister()
    check(not overlay._handles and not overlay.timer_running(), "no draw handlers and no timer")
    check(not overlay._batches and not overlay._offscreens and not overlay._shaders, "no batches, buffers or shaders")
    draws.update(view3d=0, image=0)
    redraw()
    yield 0.3
    check(draws == {"view3d": 0, "image": 0}, f"nothing draws afterwards ({draws})")


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
    for space, handle in capture_handles:
        space.draw_handler_remove(handle, 'WINDOW')
    overlay._uniforms = _uniforms
    overlay._mesh_batch = _mesh_batch
    overlay.unregister()
    session.release()
    raster.release()
    finish("SELECTION OVERLAY UI TEST")


bpy.app.timers.register(driver_for(steps()), first_interval=1.0, persistent=True)
