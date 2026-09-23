"""The selection overlay's shaders, batches and safety net, without a window (PS-091).

The shaders are drawn into offscreen targets with an identity matrix and
read back: the image editor's one-pass shader over a quad, and the 3D
view's two passes over two quads whose UVs map to separate mask islands,
which stand in for a UV seam. Pixels are counted as ants when they are
pure black or white, the default dash colours, over a mid-grey clear.

The shader, batch and mask checks need a GPU context, which background
Blender only has from 5.2 (`gpu.init()`); 4.2 to 5.1 run them windowed
under `tests/run.sh --ui`. Colour conversion, the depth offset formula
and the preferences fallback run everywhere.
"""
import os
import sys
from types import SimpleNamespace

import bpy
import gpu
import numpy as np
from mathutils import Matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    PACKAGE, RecordingLayout, check, finish, guarded, import_from, register_addon, section, skip,
)

register_addon()
common = import_from("common")
preferences = import_from("preferences")
core = import_from("gpu_passes.core")
surface = import_from("gpu_passes.surface")
raster =import_from("selection.raster")
session = import_from("selection.session")
overlay = import_from("selection.overlay")
overlay_shader = import_from("selection.overlay_shader")

SIZE = 512
GREY = 0.5


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def run(op, **props):
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result


def cancel_tick():
    if bpy.app.timers.is_registered(session._tick):
        bpy.app.timers.unregister(session._tick)
    session._pending_force = False


def available():
    if core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def ants(pixels):
    """Pure black or white pixels of an RGBA8 read back, as a boolean array."""
    rgb = pixels[..., :3]
    return (rgb < 30).all(-1) | (rgb > 225).all(-1)


def mask_texture(values):
    height, width = values.shape
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    return gpu.types.GPUTexture((width, height), format='R32F', data=gpu.types.Buffer('FLOAT', len(flat), flat))


def read_rgba8(framebuffer, width=SIZE, height=SIZE):
    buffer = gpu.types.Buffer('UBYTE', width * height * 4)
    with framebuffer.bind():
        framebuffer.read_color(0, 0, width, height, 4, 0, 'UBYTE', data=buffer)
    return np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4).astype(np.int32)


def set_uniforms(shader, wash=(0.2, 0.5, 1.0, 0.25), offset=0.0, ant_alphas=(1.0, 1.0), phase=0.0):
    """Black dashes and white gaps with the given alphas."""
    shader.uniform_float("view_projection", Matrix.Identity(4))
    shader.uniform_float("wash", wash)
    shader.uniform_float("ant_a", (0.0, 0.0, 0.0, ant_alphas[0]))
    shader.uniform_float("ant_b", (1.0, 1.0, 1.0, ant_alphas[1]))
    shader.uniform_float("params", (phase, overlay.DASH_PIXELS, offset, overlay.LINE_HALF_WIDTH))


QUAD = ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, -1, 0), (1, 1, 0), (-1, 1, 0))
QUAD_UV = ((0, 0), (1, 0), (1, 1), (0, 0), (1, 1), (0, 1))


def draw_image(mask, wash=(0.2, 0.5, 1.0, 0.25), target_format='RGBA8', ant_alphas=(1.0, 1.0), phase=0.0):
    """The image shader over the whole target, the mask stretched across it."""
    from gpu_extras.batch import batch_for_shader

    shader = overlay._shader("image")
    target = gpu.types.GPUTexture((SIZE, SIZE), format=target_format)
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    batch = batch_for_shader(shader, 'TRIS', {"position": QUAD, "uv": QUAD_UV})
    with core.saved_state():
        with framebuffer.bind():
            framebuffer.clear(color=(GREY, GREY, GREY, 1.0))
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            shader.bind()
            set_uniforms(shader, wash, ant_alphas=ant_alphas, phase=phase)
            shader.uniform_sampler("mask", mask)
            batch.draw(shader)
    return read_rgba8(framebuffer)


def test_preferences_and_colours():
    section("preferences, colours and the depth offset")
    check(common.ADDON_ID == PACKAGE and preferences.PaintSystemPreferences.bl_idname == PACKAGE,
          f"the preferences key is the add-on's package name ({common.ADDON_ID})")
    check(bpy.context.preferences.addons.get(common.ADDON_ID) is None
          and overlay.settings(bpy.context) is overlay.DEFAULTS,
          "without an add-on entry the overlay uses its defaults")
    props = preferences.PaintSystemPreferences.bl_rna.properties
    check(all(name in props for name in overlay.DEFAULTS), "every overlay setting is a preference")
    pref_defaults = {name: tuple(props[name].default_array) if props[name].is_array else (props[name].default,)
                     for name in overlay.DEFAULTS}
    ours = {name: value if isinstance(value, tuple) else (value,) for name, value in overlay.DEFAULTS.items()}
    check(all(abs(a - b) < 1e-6 for name in ours for a, b in zip(pref_defaults[name], ours[name], strict=True)),
          f"the overlay defaults match the preference defaults ({pref_defaults})")
    check(overlay.DEFAULTS["selection_wash_color"][3] == 0.0, "the selection is untinted by default")
    colours = ("selection_wash_color", "selection_ant_color_a", "selection_ant_color_b")
    check(all(props[name].subtype == 'COLOR_GAMMA' and props[name].array_length == 4 for name in colours),
          "the colours are display colours with alpha")
    calls = []
    preferences.PaintSystemPreferences.draw(SimpleNamespace(layout=RecordingLayout(calls)), bpy.context)
    drawn = [args[1] for name, args, _kwargs in calls if name == "prop"]
    declared = sorted(name for name in props.keys() if name not in ("rna_type", "bl_idname"))
    check(sorted(drawn) == declared, f"the preferences draw every preference once ({drawn})")

    got = [overlay.srgb_to_linear((v, v, v))[0] for v in (0.0, 0.04045, 0.5, 1.0)]
    want = [0.0, 0.04045 / 12.92, ((0.5 + 0.055) / 1.055) ** 2.4, 1.0]
    check(all(abs(a - b) < 1e-9 for a, b in zip(got, want)) and abs(got[2] - 0.21404) < 1e-4,
          f"sRGB to linear at 0, 0.04045, 0.5 and 1 ({[round(v, 5) for v in got]})")
    check(overlay.srgb_to_linear((0.5, 0.5, 0.5, 0.25))[2:] == (want[2], 0.25), "the alpha is not converted")

    scale = bpy.context.preferences.system.ui_scale
    if scale > 0.0:
        ant_a, ant_b, (phase, dash) = overlay.ant_style(bpy.context)
    else:
        # Background Blender reports a UI scale of 0, and draws nothing.
        scale = 1.0
        ant_a, ant_b, (phase, dash) = overlay._ant_style(overlay.DEFAULTS, scale)
    check(ant_a == (0.0, 0.0, 0.0, 1.0) and ant_b == (1.0, 1.0, 1.0, 1.0)
          and dash == overlay.DASH_PIXELS * scale and 0.0 <= phase < 2.0 * dash,
          f"ant style is the dash and gap colours, the phase and the length ({ant_a}, {ant_b}, {phase}, {dash})")

    perspective = Matrix(((1.5, 0, 0, 0), (0, 2.0, 0, 0), (0, 0, -1.002, -0.2002), (0, 0, -1, 0)))
    rv3d = SimpleNamespace(window_matrix=perspective, view_distance=10.0, view_perspective='PERSP')
    # Matrix values are float32, so compare within 1e-9.
    check(abs(overlay.clip_offset(rv3d, 1.0) - (-0.2002 * -0.00125)) < 1e-9,
          "perspective: winmat[2][3] * -0.0025 * distance / 2")
    ortho = Matrix(((0.1, 0, 0, 0), (0, 0.2, 0, 0), (0, 0, -0.001, 0), (0, 0, 0, 1)))
    rv3d = SimpleNamespace(window_matrix=ortho, view_distance=10.0, view_perspective='ORTHO')
    check(abs(overlay.clip_offset(rv3d, 2.0) - 0.00001 * 10.0) < 1e-9,
          "orthographic: 0.00001 * distance / 2 * view distance")
    rv3d.view_perspective = 'CAMERA'
    check(abs(overlay.clip_offset(rv3d, 2.0) - 0.00001 * 5.0) < 1e-9,
          "orthographic camera: the view distance comes from the matrix scale")


def test_uniforms():
    section("the draws hand the preference colours and the dash to the shaders")
    recorded = {}
    shader = SimpleNamespace(uniform_float=lambda name, value: recorded.__setitem__(name, value))
    context = SimpleNamespace(preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=2.0)))
    prefs = dict(overlay.DEFAULTS, selection_wash_color=(0.5, 0.5, 0.5, 0.25),
                 selection_ant_color_a=(1.0, 0.0, 0.0, 0.5), selection_ant_color_b=(0.0, 1.0, 0.0, 0.75))
    overlay._uniforms(shader, context, prefs, Matrix.Identity(4), 0.125)
    names = [name for _kind, name in overlay_shader.PUSH_CONSTANTS]
    check(sorted(recorded) == sorted(names), f"every push constant is set ({sorted(recorded)})")
    grey = overlay.srgb_to_linear((0.5,))[0]
    check(tuple(recorded["wash"]) == (grey, grey, grey, 0.25), f"the tint is linear with its alpha ({recorded['wash']})")
    check(tuple(recorded["ant_a"]) == (1.0, 0.0, 0.0, 0.5) and tuple(recorded["ant_b"]) == (0.0, 1.0, 0.0, 0.75),
          f"the dash and gap colours keep their alpha ({recorded['ant_a']}, {recorded['ant_b']})")
    phase, dash, offset, half_width = recorded["params"]
    check(dash == overlay.DASH_PIXELS * 2.0 and 0.0 <= phase < 2.0 * dash
          and (offset, half_width) == (0.125, overlay.LINE_HALF_WIDTH * 2.0),
          f"params are the dash, the clip offset and the half line width at UI scale 2 ({recorded['params']})")


def test_shaders():
    section("the image shader draws the tint and the ants")
    if not available():
        return
    for name in ("image", "coverage", "screen"):
        check(overlay._shader(name) is not None, f"the {name} shader compiles")
    sizes = {'MAT4': 64, 'VEC4': 16}
    total = sum(sizes[kind] for kind, _ in overlay_shader.PUSH_CONSTANTS)
    check(total == 128, f"the outline push constants take {total} bytes, Vulkan's minimum")

    n = 64
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    box = ((xx > 6) & (xx < 26) & (yy > 10) & (yy < 50)).astype(np.float32)
    distance = (1 - np.hypot((xx - 46) / 12, (yy - 32) / 20)) * 14
    t = np.clip((distance + 4) / 8, 0, 1)
    pixels = draw_image(mask_texture(np.maximum(box, t * t * (3 - 2 * t))))
    inside = pixels[int(30 * 8), int(16 * 8), :3]
    expected = [GREY * 255 * 0.75 + c * 255 * 0.25 for c in (0.2, 0.5, 1.0)]
    check(np.abs(inside - expected).max() <= 2, f"the tint inside the box ({inside.tolist()} vs {expected})")
    count = int(ants(pixels).sum())
    check(count > 500, f"ants are drawn ({count} pixels)")
    columns = np.nonzero(ants(pixels)[240])[0].tolist()
    check(columns[:2] == [47, 48], f"on the box's left edge at 48 pixels ({columns[:4]})")

    n = 1024
    yy, xx = np.mgrid[0:n, 0:n]
    half = ((xx >= 256) & (xx < 768) & (yy >= 256) & (yy < 640)).astype(np.float32)
    pixels = draw_image(mask_texture(half), wash=(0.2, 0.5, 1.0, 0.0))
    columns = np.nonzero(ants(pixels)[250])[0].tolist()
    rows = np.nonzero(ants(pixels)[:, 250])[0].tolist()
    check(columns == [127, 128, 383, 384], f"at exactly half size every edge shows ({columns})")
    check(rows == [127, 128, 319, 320], f"in both directions ({rows})")

    n = 2048
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    radius = np.hypot(xx - n / 2, yy - n / 2)
    disc = (radius < 700 + 60 * np.sin(np.arctan2(yy - n / 2, xx - n / 2) * 9)).astype(np.float32)
    disc[np.hypot((xx % 256) - 128, (yy % 256) - 128) < 40] = 0.0
    found = ants(draw_image(mask_texture(disc), wash=(0.2, 0.5, 1.0, 0.0)))
    holes = [(cx, cy) for cx in range(128, n, 256) for cy in range(128, n, 256)
             if np.hypot(cx - n / 2, cy - n / 2) < 560]
    outlined = [found[cy // 4 - 14:cy // 4 + 14, cx // 4 - 14:cx // 4 + 14].sum() > 0 for cx, cy in holes]
    check(holes and all(outlined), f"a 2048 mask minified to 512 outlines each hole ({sum(outlined)} of {len(holes)})")

    target_format = 'SRGB8_A8'
    grey = overlay.srgb_to_linear((0.5, 0.5, 0.5))
    pixels = draw_image(mask_texture(np.ones((8, 8), np.float32)), wash=(*grey, 1.0), target_format=target_format)
    centre = pixels[SIZE // 2, SIZE // 2, :3].tolist()
    check(all(abs(c - 127.5) <= 1.5 for c in centre),
          f"a preference grey of 0.5 reads 127 through an sRGB target ({centre})")


def test_ants_over_wash():
    section("the ants' soft edge takes no colour from the wash")
    if not available():
        return
    # A 64 texel disc magnified 8 times, so the outline crosses pixels at
    # every fraction and its edge is partly covered.
    n, scale = 64, SIZE // 64
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    disc = (np.hypot(xx - 32, yy - 32) < 18).astype(np.float32)
    mask = mask_texture(disc)
    # The mask as the image shader filters it at each pixel centre.
    t = (np.arange(SIZE) + 0.5) / scale - 0.5
    low = np.clip(np.floor(t).astype(int), 0, n - 1)
    high = np.clip(low + 1, 0, n - 1)
    w = t - np.floor(t)
    rows = disc[low] * (1 - w)[:, None] + disc[high] * w[:, None]
    m = rows[:, low] * (1 - w) + rows[:, high] * w

    rgb = draw_image(mask, wash=(0.0, 0.0, 1.0, 0.0))[..., :3]
    grey = round(GREY * 255)
    partial = int(((np.abs(rgb - grey) > 3).any(-1) & ~ants(rgb)).sum())
    spread = int((rgb.max(-1) - rgb.min(-1)).max())
    check(partial > 100 and spread <= 2,
          f"a transparent blue wash leaves the edge grey ({partial} partly covered pixels, channels differ by {spread})")

    wash = (1.0, 0.0, 0.0, 0.5)
    alpha = (wash[3] * np.clip(m, 0.0, 1.0))[..., None]
    under = np.array(wash[:3]) * alpha + GREY * (1.0 - alpha)
    for ant_alpha in (1.0, 0.5):
        rgb = draw_image(mask, wash=wash, ant_alphas=(ant_alpha, ant_alpha))[..., :3] / 255.0
        # Each pixel should be the wash over the grey, with a black or
        # white ant over that. `cover` is how much of the ant shows,
        # which the ants' alpha caps.
        error = np.full(m.shape, np.inf)
        for ant in (0.0, 1.0):
            cover = (rgb[..., 1] - under[..., 1]) / (ant - under[..., 1])
            fits = np.abs(under + cover[..., None] * (ant - under) - rgb).max(-1)
            error = np.where((cover > -0.02) & (cover < ant_alpha + 0.02), np.minimum(error, fits), error)
        edge = int((np.abs(rgb - under).max(-1) > 2 / 255).sum())
        worst = float(error.max()) * 255
        check(edge > 500 and worst <= 3.0,
              f"at ant alpha {ant_alpha} every pixel is an ant colour over the wash "
              f"({edge} edge pixels, worst off by {worst:.1f}/255)")


def test_ant_alpha():
    section("the alpha of the dash and gap colours")
    if not available():
        return
    n = 64
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    mask = mask_texture(((xx > 16) & (xx < 48) & (yy > 16) & (yy < 48)).astype(np.float32))
    no_wash = (0.0, 0.0, 0.0, 0.0)
    grey = GREY * 255

    rgb = draw_image(mask, wash=no_wash, ant_alphas=(0.0, 0.0))[..., :3]
    check(np.abs(rgb - grey).max() <= 1, f"transparent ants draw nothing ({rgb.min()} to {rgb.max()})")
    rgb = draw_image(mask, wash=no_wash, ant_alphas=(0.5, 0.5))[..., :3]
    darkest, lightest = 0.5 * grey, 0.5 * grey + 0.5 * 255
    check(abs(rgb.min() - darkest) <= 2 and abs(rgb.max() - lightest) <= 2,
          f"half transparent ants show the grey through ({rgb.min()} to {rgb.max()}, "
          f"want {darkest:.0f} to {lightest:.0f})")
    rgb = draw_image(mask, wash=no_wash, ant_alphas=(1.0, 0.0))[..., :3]
    check(rgb.min() <= 2 and rgb.max() <= grey + 1,
          f"an opaque dash colour and a transparent gap colour draw only the dashes ({rgb.min()} to {rgb.max()})")

    # Fragment centres fall on half pixels and the dash is 4 pixels, so no
    # fragment sits on a dash boundary and a shift of one dash length swaps
    # every dash for a gap.
    shifted = draw_image(mask, wash=no_wash, ant_alphas=(1.0, 0.0), phase=overlay.DASH_PIXELS)[..., :3]
    dashes, dashes_shifted = (rgb < 30).all(-1), (shifted < 30).all(-1)
    check(dashes.sum() > 100 and dashes_shifted.sum() > 100 and not (dashes & dashes_shifted).any(),
          f"a phase of one dash length moves the dashes onto the gaps ({int(dashes.sum())} and "
          f"{int(dashes_shifted.sum())} dash pixels, {int((dashes & dashes_shifted).sum())} in both)")


def test_two_passes_draw_no_seam():
    section("the 3D view's two passes draw no ants along a UV seam")
    if not available():
        return
    from gpu_extras.batch import batch_for_shader

    # Two islands of a 64 texel mask, both selected, with unselected gutter
    # texels around them. The left quad shows texels 0..27 (gutter 0..3)
    # and the right quad texels 36..63 (gutter 60..63), so they meet at
    # the middle of the target like two faces across a seam.
    n = 64
    values = np.zeros((n, n), np.float32)
    values[:, 4:28] = 1.0
    values[:, 36:60] = 1.0
    mask = mask_texture(values)
    left_u, right_u = (0.0, 27.999 / n), (36.001 / n, 1.0)
    position, uv = [], []
    for (x0, x1), (u0, u1) in (((-1.0, 0.0), left_u), ((0.0, 1.0), right_u)):
        position += [(x0, -1, 0), (x1, -1, 0), (x1, 1, 0), (x0, -1, 0), (x1, 1, 0), (x0, 1, 0)]
        uv += [(u0, 0.01), (u1, 0.01), (u1, 0.99), (u0, 0.01), (u1, 0.99), (u0, 0.99)]
    coverage, screen = overlay._shader("coverage"), overlay._shader("screen")
    batch = batch_for_shader(coverage, 'TRIS', {"position": position, "uv": uv})
    offscreen = gpu.types.GPUOffScreen(SIZE, SIZE, format='RGBA16F')
    target = gpu.types.GPUTexture((SIZE, SIZE), format='RGBA8')
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    with core.saved_state():
        with offscreen.bind():
            gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            coverage.bind()
            coverage.uniform_float("view_projection", Matrix.Identity(4))
            coverage.uniform_float("tile", (0.0, 0.0, 0.0, 0.0))
            coverage.uniform_sampler("mask", mask)
            batch.draw(coverage)
        with framebuffer.bind():
            framebuffer.clear(color=(GREY, GREY, GREY, 1.0))
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            screen.bind()
            set_uniforms(screen, wash=(0.2, 0.5, 1.0, 0.0))
            screen.uniform_sampler("screen", offscreen.texture_color)
            batch.draw(screen)
    found = ants(read_rgba8(framebuffer))
    middle = int(found[:, SIZE // 2 - 4:SIZE // 2 + 4].sum())
    columns = np.nonzero(found[SIZE // 2])[0].tolist()
    check(middle == 0, f"no ants where the two islands meet ({middle} pixels)")
    # Gutter to island edges on screen: 4/28 of the left half, and 24/28
    # of the right half past the middle.
    left_edge, right_edge = 4 / 28 * SIZE / 2, SIZE / 2 + 24 / 28 * SIZE / 2
    check(any(abs(c - left_edge) <= 2 for c in columns) and any(abs(c - right_edge) <= 2 for c in columns),
          f"ants where an island meets its gutter ({columns})")
    offscreen.free()


def setup_layer():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    run(bpy.ops.paint_system.setup_material)
    run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    layer = tree().nodes.active
    layer.image = bpy.data.images.new("PS Overlay Layer", 256, 256)
    other = bpy.data.materials.new("PS Overlay Other")
    obj.data.materials.append(other)
    for polygon in obj.data.polygons:
        if polygon.normal.z > 0.9:
            polygon.material_index = 1
    second = obj.data.uv_layers.new(name="PS Overlay Second")
    for loop in second.data:
        loop.uv = (0.25, 0.75)
    bpy.context.view_layer.update()
    return layer


LAYER = setup_layer()

builds = []
_mesh_batch = overlay._mesh_batch


def _counting_mesh_batch(obj, uv_map, tree, depsgraph):
    builds.append(uv_map)
    return _mesh_batch(obj, uv_map, tree, depsgraph)


overlay._mesh_batch = _counting_mesh_batch

batch_arrays = []
_batch_for_shader = overlay.batch_for_shader


def _recording_batch_for_shader(shader, kind, content, **kwargs):
    batch_arrays.append({name: np.asarray(values) for name, values in content.items()})
    return _batch_for_shader(shader, kind, content, **kwargs)


overlay.batch_for_shader = _recording_batch_for_shader


def test_mesh_batch():
    section("the mesh batch holds the faces that use the tree, through the layer's UV map")
    if not available():
        return
    obj = cube()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    overlay.invalidate_all()
    builds.clear()
    batch_arrays.clear()
    LAYER.uv_map = "PS Overlay Second"
    session.sync(force=True)
    state = session.current()
    cancel_tick()
    try:
        check(state.uv_map == "PS Overlay Second", f"the state names the layer's UV map ({state.uv_map})")
        batch = overlay._cached_batch(obj, state.uv_map, tree(), depsgraph)
        arrays = batch_arrays[-1] if batch_arrays else {}
        check(batch is not None and len(arrays.get("position", ())) == 30,
              f"10 of the cube's 12 triangles use the tree ({len(arrays.get('position', ()))} vertices)")
        check(np.allclose(arrays.get("uv", np.zeros((1, 2))), (0.25, 0.75)), "the UVs come from the state's map")
        overlay._cached_batch(obj, state.uv_map, tree(), depsgraph)
        check(len(builds) == 1, f"a second draw reuses the batch ({len(builds)} builds)")
        check(overlay._mesh_batch(obj, "PS Overlay Missing", tree(), depsgraph) is None, "a missing UV map draws nothing")
    finally:
        LAYER.uv_map = ""
        session.sync(force=True)
        cancel_tick()

    overlay._cached_batch(obj, "UVMap", tree(), bpy.context.evaluated_depsgraph_get())
    overlay.sync(session.State(object_uid=obj.session_uid + 1), None)
    check(not overlay._batches, "a state for another object drops the batch")
    check(not hasattr(overlay, "invalidate_object"), "no handler drops batches per object any more")


def cancel_surface_tick():
    """Unregister the surface tick, keeping its requests for the test to run."""
    if bpy.app.timers.is_registered(surface._tick):
        bpy.app.timers.unregister(surface._tick)


def draw_batch(obj, uv_map="UVMap"):
    """One draw's batch lookup; returns whether it queued a surface request."""
    surface._requests.clear()
    cancel_surface_tick()
    overlay._cached_batch(obj, uv_map, tree(), bpy.context.evaluated_depsgraph_get())
    requested = bool(surface._requests)
    cancel_surface_tick()
    return requested


def run_surface_tick():
    surface._tick()
    cancel_tick()


def test_batch_follows_surface_key():
    section("the batch is rebuilt when the surface key changes, not on every geometry update")
    if not available():
        return
    obj = cube()
    overlay.invalidate_all()
    surface.forget()
    builds.clear()
    batch_arrays.clear()
    draw_batch(obj)
    check(len(builds) == 1, f"the first draw builds ({len(builds)} builds)")

    obj.data.update()
    bpy.context.view_layer.update()
    requested = draw_batch(obj)
    check(requested and len(builds) == 1, f"after a geometry update a draw uses the batch and requests a resolve "
                                          f"({len(builds)} builds, requested {requested})")
    run_surface_tick()
    requested = draw_batch(obj)
    check(not requested and len(builds) == 1,
          f"an unchanged key keeps the batch ({len(builds)} builds, requested {requested})")

    modifier = obj.modifiers.new("PS Overlay Subsurf", 'SUBSURF')
    modifier.levels = 1
    bpy.context.view_layer.update()
    try:
        requested = draw_batch(obj)
        check(requested and len(builds) == 1,
              f"a content change draws the cached batch once and requests ({len(builds)} builds)")
        run_surface_tick()
        draw_batch(obj)
        draw_batch(obj)
        count = len(batch_arrays[-1]["position"]) if batch_arrays else 0
        check(len(builds) == 2 and count == 120,
              f"after the tick it rebuilds once from the subdivided mesh ({len(builds)} builds, {count} vertices)")
    finally:
        obj.modifiers.remove(modifier)
        bpy.context.view_layer.update()
        draw_batch(obj)
        run_surface_tick()
        draw_batch(obj)

    section("an evaluated mesh without the UV map builds once and asks nothing")
    remesh = obj.modifiers.new("PS Overlay Remesh", 'REMESH')
    bpy.context.view_layer.update()
    try:
        surface.forget()
        overlay.invalidate_all()
        builds.clear()
        requests = sum(draw_batch(obj) for _ in range(5))
        check(len(builds) == 1 and requests == 0 and not bpy.app.timers.is_registered(surface._tick),
              f"five draws: {len(builds)} builds and {requests} requests")
    finally:
        obj.modifiers.remove(remesh)
        bpy.context.view_layer.update()

    section("a surface entry that loses its arrays costs no rebuild or request")
    others = []
    for index in range(surface.ENTRY_LIMIT):
        other = bpy.data.objects.new(f"PS Overlay Other {index}", obj.data.copy())
        bpy.context.scene.collection.objects.link(other)
        others.append(other)
    bpy.context.view_layer.update()
    try:
        surface.forget()
        overlay.invalidate_all()
        builds.clear()
        draw_batch(obj)
        for other in others:
            surface.resolve_key(other, "UVMap")
        layer = surface._layer(bpy.context.evaluated_depsgraph_get())
        check(surface._entries[(obj.session_uid, "UVMap", layer)].arrays is None,
              "resolving ENTRY_LIMIT other objects drops the arrays of the batch's entry")
        requested = [draw_batch(obj) for _ in range(3)]
        check(not any(requested) and len(builds) == 1,
              f"later draws hit the batch without a request ({len(builds)} builds, requests {requested})")
    finally:
        for other in others:
            mesh = other.data
            bpy.data.objects.remove(other)
            bpy.data.meshes.remove(mesh)
        bpy.context.view_layer.update()
        surface.forget()


def test_target_mask():
    section("the draw only shows a mask that matches the synced state")
    if not available():
        return
    t = tree()
    t.active_layer_index = t.nodes.find(LAYER.name)
    image = LAYER.paint_image
    t.selection.clear()
    t.selection.add_op('BOX', points=[(0.2, 0.2), (0.7, 0.6)], feather=2.0)
    state = session.sync(force=True)
    cancel_tick()
    try:
        mask = overlay._target_mask(t, state, image)
        check(state.active and mask is not None and mask.key == state.digest
              and not bpy.app.timers.is_registered(session._tick),
              f"a synced selection shows its mask without a notify ({state.reason})")
        other = bpy.data.images.new("PS Overlay Unrelated", 256, 256)
        check(overlay._target_mask(t, state, other) is None and not bpy.app.timers.is_registered(session._tick),
              "another image shows nothing and needs no sync")
        bpy.data.images.remove(other)

        t.selection.add_op('INVERT')
        check(overlay._target_mask(t, state, image) is None and bpy.app.timers.is_registered(session._tick),
              "ops edited without a notify show nothing and schedule a sync")
        cancel_tick()
        state = session.sync()
        check(overlay._target_mask(t, state, image) is not None, "which shows the new mask")

        image.scale(128, 64)
        check(overlay._target_mask(t, state, image) is None and bpy.app.timers.is_registered(session._tick),
              "a resized image shows nothing and schedules a sync")
        cancel_tick()
        state = session.sync()
        check(state.size == (128, 64) and overlay._target_mask(t, state, image) is not None,
              f"which rebuilds at the new size ({state.size})")
    finally:
        t.selection.clear()
        session.sync(force=True)
        cancel_tick()


def test_wiring():
    section("the session and the depsgraph handler reach the overlay")
    reaches = []
    sync = overlay.sync

    def counting_sync(state, target):
        reaches.append(state)
        sync(state, target)

    overlay.sync = counting_sync
    try:
        session.sync(force=True)
        cancel_tick()
        check(len(reaches) == 1, f"a forced sync hands the state to the overlay ({len(reaches)} calls)")
        session.sync()
        cancel_tick()
        check(len(reaches) == 1, f"an unchanged sync does not ({len(reaches)} calls)")
    finally:
        overlay.sync = sync

    if not available():
        return
    obj = cube()
    overlay._cached_batch(obj, "UVMap", tree(), bpy.context.evaluated_depsgraph_get())
    check(any(key[0] == obj.session_uid for key in overlay._batches), "the object has a batch")
    modifier = obj.modifiers.new("PS Overlay Wiring", 'SUBSURF')
    try:
        bpy.context.view_layer.update()
        check(any(key[0] == obj.session_uid for key in overlay._batches)
              and not surface.peek_key(obj, "UVMap", bpy.context.evaluated_depsgraph_get())[1],
              "a geometry update of the object keeps its batch and marks its surface suspect")
    finally:
        obj.modifiers.remove(modifier)
        bpy.context.view_layer.update()
        cancel_tick()


guarded(test_preferences_and_colours)
guarded(test_uniforms)
guarded(test_shaders)
guarded(test_ants_over_wash)
guarded(test_ant_alpha)
guarded(test_two_passes_draw_no_seam)
guarded(test_mesh_batch)
guarded(test_batch_follows_surface_key)
guarded(test_target_mask)
guarded(test_wiring)
# Free the shaders, batches and masks while the GPU context is still up;
# freeing them at interpreter shutdown segfaults a background Blender.
overlay.batch_for_shader = _batch_for_shader
overlay._mesh_batch = _mesh_batch
overlay.unregister()
session.release()
raster.release()
surface.release()
finish("SELECTION OVERLAY TEST")
