"""Draw the live selection over the 3D view and the image editor (PS-091).

The overlay only draws. It never touches a material, never builds a mask
and never changes the selection: each draw callback reads the state the
session last synced (`session.current()`), peeks the cached mask and
draws it, or draws nothing. When what it would draw no longer matches
the state, it calls `session.notify()` and the next tick catches up.
That is the safety net for a writer that forgot to notify, and the only
thing that sees an image resized with `Image.scale`, which reports no
event.

- The 3D view shows the selection in Texture Paint mode only, on the
  object the selection applies to, while `show_selection_3d` is on. The
  mesh is drawn twice (see `overlay_shader`): coverage into an offscreen
  buffer the size of the region, then a screen pass with a small clip
  depth offset so it does not fight the surface.
- The image editor shows it over the image the selection applies to, in
  View and Paint mode.
- While either is visible, an 8 Hz timer tags just those areas for redraw
  so the ants march. It stops on the first tick that finds nothing to
  show.

The mesh batch is cached per object in local space, so moving the object
costs nothing, together with the surface key it was built from
(`gpu_passes.surface`). A draw only peeks at the key. While the key is not
fresh, after a geometry update, an undo or a frame change, the draw uses
the cached batch and requests a resolve; the batch is rebuilt once the
resolved key differs. A texture paint stroke, which reports a geometry
update on 5.3, therefore rebuilds nothing, and a real surface change
shows the previous batch for one frame. `handlers.node_tree_handlers`
drops every batch when a file is read.
"""
import functools
import logging
import time

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..common import ADDON_ID
from ..context import get_active_tree, get_ps_object
from ..gpu_passes import core, surface
from . import overlay_shader, raster

log = logging.getLogger(__name__)

REDRAW_INTERVAL = 0.125
"""Seconds between redraws that march the ants."""

DASH_PIXELS = 4.0
"""Length of one dash, in pixels at a UI scale of 1."""

DASH_SPEED = 16.0
"""Pixels the dashes travel per second, at a UI scale of 1."""

LINE_HALF_WIDTH = 1.0
"""Half the width of the ants, in pixels at a UI scale of 1."""

DEPTH_OFFSET = 1.0
"""Distance handed to the polygon offset; Blender's own edit overlays use 1."""

DEFAULTS = {
    "show_selection_3d": True,
    "selection_wash_color": (0.25, 0.55, 1.0),
    "selection_wash_opacity": 0.2,
    "selection_ant_color_a": (0.0, 0.0, 0.0),
    "selection_ant_color_b": (1.0, 1.0, 1.0),
}
"""The preferences the overlay reads, with the values used when the add-on has no preferences entry."""

_SHADER_FACTORIES = {
    "image": overlay_shader.create_image_shader,
    "coverage": overlay_shader.create_coverage_shader,
    "screen": overlay_shader.create_screen_shader,
}

_handles: list[tuple[type, object]] = []
_shaders: dict[str, gpu.types.GPUShader] = {}
# Region pointer -> the coverage buffer of that region.
_offscreens: dict[int, gpu.types.GPUOffScreen] = {}
# (object session_uid, UV map, per slot whether it uses the tree) -> (surface key or None, batch or None
# without triangles).
_batches: dict[tuple, tuple[bytes | None, gpu.types.GPUBatch | None]] = {}


def srgb_to_linear(color) -> tuple[float, float, float]:
    """The first three channels of display colour *color* as the linear values a shader must write.

    The viewport and the image editor treat what a `GPUShaderCreateInfo`
    shader writes as linear and encode it to sRGB, so a preference colour
    drawn as is would come out lighter than its swatch.
    """
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color[:3])


def settings(context) -> dict:
    """The overlay preferences by name, or `DEFAULTS` when the add-on has no preferences entry."""
    addon = context.preferences.addons.get(ADDON_ID)
    prefs = addon.preferences if addon is not None else None
    if prefs is None:
        return DEFAULTS
    return {name: getattr(prefs, name) for name in DEFAULTS}


def _ant_style(prefs: dict, scale: float):
    dash = DASH_PIXELS * scale
    phase = (time.monotonic() * DASH_SPEED * scale) % (2.0 * dash)
    return ((*srgb_to_linear(prefs["selection_ant_color_a"]), phase),
            (*srgb_to_linear(prefs["selection_ant_color_b"]), dash))


def ant_style(context) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """The `ant_a` and `ant_b` push constants for `overlay_shader.ANT_GLSL`, as of now.

    Linear RGB plus the dash phase, and linear RGB plus the dash length,
    both in pixels scaled by the UI scale. A shader that draws its own
    ants with these crawls in step with the overlay.
    """
    return _ant_style(settings(context), context.preferences.system.ui_scale)


def clip_offset(rv3d, distance: float) -> float:
    """Blender's polygon offset (`GPU_polygon_offset_calc`) as a shift of clip-space z.

    *rv3d* needs `window_matrix`, `view_distance` and `view_perspective`.
    The matrix is indexed row first, as `mathutils` does.
    """
    winmat = rv3d.window_matrix
    distance *= 0.5
    if winmat[3][3] > 0.5:
        view_distance = rv3d.view_distance
        if rv3d.view_perspective == 'CAMERA':
            view_distance = 1.0 / max(abs(winmat[0][0]), abs(winmat[1][1]))
        return 0.00001 * distance * view_distance
    return winmat[2][3] * -0.0025 * distance


def invalidate_all() -> None:
    """Drop every batch and coverage buffer; after a file read, whose objects and regions are new."""
    _batches.clear()
    _offscreens.clear()


def sync(state, target) -> None:
    """Follow a new session state: keep only the batches of its object and march the ants if visible.

    Called by the session after each sync that changed something; *target*
    is unused and there for the consumer contract.
    """
    for key in [key for key in _batches if key[0] != state.object_uid]:
        del _batches[key]
    if state.active and _overlay_areas():
        ensure_timer()


def ensure_timer() -> None:
    """Start the redraw timer if it is not running."""
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=REDRAW_INTERVAL)


def timer_running() -> bool:
    return bpy.app.timers.is_registered(_tick)


def _shader(name: str) -> gpu.types.GPUShader:
    shader = _shaders.get(name)
    if shader is None:
        shader = _shaders[name] = _SHADER_FACTORIES[name]()
    return shader


# ── What to draw ─────────────────────────────────────────────────────

def _target_mask(tree, state, image) -> raster.SelectionMask | None:
    """The cached mask to draw over *image*, or None; notifies the session when it is out of step.

    *tree* is the active tree where the draw happens. Nothing is drawn
    unless the state is active and names that tree and image. An image
    whose size no longer matches the state (`Image.scale` reports nothing)
    and a mask that is missing or was built for other ops both schedule a
    sync.
    """
    from . import session

    if not state.active or tree is None or image is None:
        return None
    if tree.session_uid != state.tree_uid or image.session_uid != state.image_uid:
        return None
    if raster.image_size(image, state.tile) != state.size:
        session.notify()
        return None
    # peek_mask looks the mask up by the digest of the ops as they are now,
    # with the surface keys a draw may read without resolving them.
    mask = raster.peek_mask(tree.selection, state.size, state.tile,
                            surface_key=functools.partial(raster.view_key, peek=True))
    if mask is None or mask.key != state.digest:
        session.notify()
        return None
    return mask


def _slot_uses(obj, tree) -> tuple[bool, ...]:
    return tuple(slot.material is not None and slot.material.paint_system.tree == tree
                 for slot in obj.material_slots)


def _mesh_batch(obj, uv_map: str, tree, depsgraph) -> gpu.types.GPUBatch | None:
    """The evaluated mesh of *obj* in local space, the faces whose material uses *tree* only.

    Triangles with `position` and `uv` from *uv_map*. None when nothing is
    left to draw or the map is missing.
    """
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
    except RuntimeError as error:
        log.warning("Could not evaluate %r for the selection overlay: %s", obj.name, error)
        return None
    if mesh is None:
        return None
    try:
        mesh.calc_loop_triangles()
        layer = mesh.uv_layers.get(uv_map)
        if not len(mesh.loop_triangles) or layer is None:
            return None
        corners = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get('loops', corners)
        material_index = np.empty(len(mesh.loop_triangles), dtype=np.int32)
        mesh.loop_triangles.foreach_get('material_index', material_index)
        uvs = np.empty(len(layer.uv) * 2, dtype=np.float32)
        layer.uv.foreach_get('vector', uvs)
        vertex_of_corner = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', vertex_of_corner)
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', positions)
    finally:
        evaluated.to_mesh_clear()
    uses = _slot_uses(obj, tree)
    if uses:
        keep = np.array(uses, dtype=bool)[np.clip(material_index, 0, len(uses) - 1)]
        corners = corners.reshape(-1, 3)[keep].ravel()
    if not len(corners):
        return None
    return batch_for_shader(_shader("coverage"), 'TRIS', {
        "position": positions.reshape(-1, 3)[vertex_of_corner[corners]],
        "uv": uvs.reshape(-1, 2)[corners],
    })


def _cached_batch(obj, uv_map: str, tree, depsgraph) -> gpu.types.GPUBatch | None:
    """The mesh batch to draw now, built at most once per surface key.

    A cached batch whose key is not fresh is drawn as it is while the
    timer resolves the key, also when the key is None or its entry was
    dropped. A fresh key equal to the cached one, None included, draws the
    cached batch. Anything else resolves the key and builds.
    """
    key = (obj.session_uid, uv_map, _slot_uses(obj, tree))
    cached = _batches.get(key)
    if cached is not None:
        surface_key, fresh = surface.peek_key(obj, uv_map, depsgraph)
        if not fresh:
            surface.request(obj, uv_map)
            return cached[1]
        if cached[0] == surface_key:
            return cached[1]
    # A miss builds in the draw; resolving the key costs little next to it.
    surface_key = surface.resolve_key(obj, uv_map, depsgraph)
    _batches[key] = (surface_key, _mesh_batch(obj, uv_map, tree, depsgraph))
    return _batches[key][1]


def _offscreen(region) -> gpu.types.GPUOffScreen | None:
    """The coverage buffer of *region*, made again when the region changed size."""
    key = region.as_pointer()
    offscreen = _offscreens.get(key)
    if offscreen is not None and (offscreen.width, offscreen.height) == (region.width, region.height):
        return offscreen
    _offscreens.pop(key, None)
    if region.width <= 0 or region.height <= 0:
        return None
    try:
        offscreen = gpu.types.GPUOffScreen(region.width, region.height, format='RGBA16F')
    except RuntimeError as error:
        log.debug("Could not allocate the selection coverage buffer: %s", error)
        return None
    _offscreens[key] = offscreen
    return offscreen


def _prune_offscreens(pointers: set[int]) -> None:
    """Free the coverage buffers of regions not in *pointers*."""
    for key in [key for key in _offscreens if key not in pointers]:
        del _offscreens[key]


# ── Drawing ──────────────────────────────────────────────────────────

class _DrawState:
    """Blend, depth test and depth write, restored on exit."""

    def __enter__(self):
        self.blend = gpu.state.blend_get()
        self.depth_test = gpu.state.depth_test_get()
        self.depth_mask = gpu.state.depth_mask_get()
        return self

    def __exit__(self, *exc):
        gpu.state.blend_set(self.blend)
        gpu.state.depth_test_set(self.depth_test)
        gpu.state.depth_mask_set(self.depth_mask)
        return False


def _uniforms(shader, context, prefs: dict, view_projection, tile: int, offset: float) -> None:
    """Set the `overlay_shader.PUSH_CONSTANTS` of *shader*."""
    scale = context.preferences.system.ui_scale
    ant_a, ant_b = _ant_style(prefs, scale)
    shader.uniform_float("view_projection", view_projection)
    shader.uniform_float("wash", (*srgb_to_linear(prefs["selection_wash_color"]), prefs["selection_wash_opacity"]))
    shader.uniform_float("ant_a", ant_a)
    shader.uniform_float("ant_b", ant_b)
    shader.uniform_float("params", (*core.tile_offset(tile), offset, LINE_HALF_WIDTH * scale))


def _draw_view3d() -> None:
    from . import session

    context = bpy.context
    state = session.current()
    if not (state.active and state.paint_mode) or context.scene.session_uid != state.scene_uid:
        return
    obj = get_ps_object(context.active_object)
    if obj is None or obj.session_uid != state.object_uid:
        return
    prefs = settings(context)
    if not prefs["show_selection_3d"]:
        return
    tree = get_active_tree(context)
    layer = tree.nodes.active if tree is not None else None
    mask = _target_mask(tree, state, getattr(layer, 'paint_image', None))
    if mask is None:
        return
    ensure_timer()
    batch = _cached_batch(obj, state.uv_map, tree, context.evaluated_depsgraph_get())
    offscreen = _offscreen(context.region)
    if batch is None or offscreen is None:
        return
    rv3d = context.region_data
    view_projection = rv3d.perspective_matrix @ obj.matrix_world
    coverage, screen = _shader("coverage"), _shader("screen")
    with _DrawState():
        with offscreen.bind():
            gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            coverage.bind()
            coverage.uniform_float("view_projection", view_projection)
            coverage.uniform_float("tile", (*core.tile_offset(state.tile), 0.0, 0.0))
            coverage.uniform_sampler("mask", mask.texture)
            batch.draw(coverage)
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(False)
        screen.bind()
        _uniforms(screen, context, prefs, view_projection, state.tile, clip_offset(rv3d, DEPTH_OFFSET))
        screen.uniform_sampler("screen", offscreen.texture_color)
        batch.draw(screen)


def _draw_image_editor() -> None:
    from . import session

    context = bpy.context
    state = session.current()
    if not state.active:
        return
    image = context.space_data.image
    if image is None or image.session_uid != state.image_uid:
        return
    mask = _target_mask(get_active_tree(context), state, image)
    if mask is None:
        return
    ensure_timer()
    view2d = context.region.view2d
    # region_to_view returns floats, where view_to_region rounds to whole
    # pixels. View coordinates are UV units, also for a non-square image.
    x0, y0 = view2d.region_to_view(0.0, 0.0)
    x1, y1 = view2d.region_to_view(1000.0, 1000.0)
    sx, sy = 1000.0 / (x1 - x0), 1000.0 / (y1 - y0)
    ox, oy = core.tile_offset(state.tile)
    px0, py0 = (ox - x0) * sx, (oy - y0) * sy
    px1, py1 = (ox + 1.0 - x0) * sx, (oy + 1.0 - y0) * sy
    shader = _shader("image")
    batch = batch_for_shader(shader, 'TRIS', {
        "position": ((px0, py0, 0.0), (px1, py0, 0.0), (px1, py1, 0.0),
                     (px0, py0, 0.0), (px1, py1, 0.0), (px0, py1, 0.0)),
        "uv": ((ox, oy), (ox + 1.0, oy), (ox + 1.0, oy + 1.0),
               (ox, oy), (ox + 1.0, oy + 1.0), (ox, oy + 1.0)),
    })
    view_projection = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    with _DrawState():
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        shader.bind()
        _uniforms(shader, context, settings(context), view_projection, state.tile, 0.0)
        shader.uniform_sampler("mask", mask.texture)
        batch.draw(shader)


# ── Redraw timer ─────────────────────────────────────────────────────

def _overlay_areas() -> list[bpy.types.Area]:
    """The areas of every window that show the selection now."""
    from . import session

    state = session.current()
    if not state.active:
        return []
    context = bpy.context
    show_3d = state.paint_mode and settings(context)["show_selection_3d"]
    areas = []
    for window in context.window_manager.windows:
        if window.scene.session_uid != state.scene_uid:
            continue
        obj = get_ps_object(window.view_layer.objects.active)
        on_target = show_3d and obj is not None and obj.session_uid == state.object_uid
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                if on_target:
                    areas.append(area)
            elif area.type == 'IMAGE_EDITOR':
                image = area.spaces.active.image
                if image is not None and image.session_uid == state.image_uid:
                    areas.append(area)
    return areas


def _tick() -> float | None:
    areas = _overlay_areas()
    for area in areas:
        area.tag_redraw()
    _prune_offscreens({region.as_pointer() for area in areas if area.type == 'VIEW_3D'
                       for region in area.regions if region.type == 'WINDOW'})
    return REDRAW_INTERVAL if areas else None


def register() -> None:
    _handles.append((bpy.types.SpaceView3D, bpy.types.SpaceView3D.draw_handler_add(
        _draw_view3d, (), 'WINDOW', 'POST_VIEW')))
    _handles.append((bpy.types.SpaceImageEditor, bpy.types.SpaceImageEditor.draw_handler_add(
        _draw_image_editor, (), 'WINDOW', 'POST_PIXEL')))


def unregister() -> None:
    """Remove the draw handlers and the timer, and free every GPU object while the context is up."""
    for space, handle in _handles:
        space.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    if timer_running():
        bpy.app.timers.unregister(_tick)
    _batches.clear()
    _offscreens.clear()
    _shaders.clear()
