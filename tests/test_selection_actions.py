"""Clear, Fill and Invert Colors edit exactly what the brush could paint (PS-052).

`filters.actions` decides what an action may touch and how far the
selection lets it reach; `ops.pixel_ops` wraps that in an operator. What
these check is the scope rule and the refusals, because both are the
safety of the feature: an action that quietly widens its reach erases
work, and one that quietly does nothing looks broken.

The GPU parts need a context. Blender 5.2 added `gpu.init()`, which
builds one in background mode, so they run in the ordinary headless job
there; background 4.2 to 5.1 skip them and the windowed job covers them.
The refusals and the polls need no GPU and always run.
"""
import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, guarded, import_from, register_addon, section, skip  # noqa: E402

register_addon()
gpu_core = import_from("gpu_passes.core")
actions = import_from("filters.actions")
brush_color = import_from("filters.brush_color")
core = import_from("filters.core")
raster = import_from("selection.raster")
undo_pixels = import_from("undo.pixels")

SIZE = 64


def cube():
    return bpy.data.objects["Cube"]


def tree():
    return cube().active_material.paint_system.tree


def run(op, **props):
    result = op('EXEC_DEFAULT', True, **props)
    bpy.context.view_layer.update()
    return result


def setup():
    obj = cube()
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    run(bpy.ops.paint_system.setup_material)
    run(bpy.ops.paint_system.add_layer, layer_type='IMAGE', resolution='1024')
    layer = tree().nodes.active
    layer.image = bpy.data.images.new("PS Action Canvas", SIZE, SIZE, alpha=True)
    paint = layer.name
    run(bpy.ops.paint_system.add_layer, layer_type='SOLID_COLOR')
    solid = tree().nodes.active.name
    return paint, solid


PAINT, SOLID = setup()
# Starts the background GPU context where one exists (5.2 and later).
HAS_GPU = gpu_core.gpu_available()


def available():
    if HAS_GPU:
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def select(name):
    t = tree()
    t.active_layer_index = t.nodes.find(name)


def canvas():
    return tree().nodes[PAINT].image


def paint_pixels(values=None):
    """Fill the paint layer with *values*, or with repeatable random bytes."""
    image = canvas()
    if values is None:
        rng = np.random.default_rng(3)
        values = rng.integers(0, 256, size=SIZE * SIZE * 4).astype(np.float32) / 255.0
    image.pixels.foreach_set(np.asarray(values, dtype=np.float32).ravel())
    image.update()
    return read()


def read():
    image = canvas()
    values = np.empty(SIZE * SIZE * 4, dtype=np.float32)
    image.pixels.foreach_get(values)
    return values


def as_bytes(values):
    return np.rint(np.asarray(values, dtype=np.float64) * 255.0).astype(np.int32)


def reset():
    """A clean tree: the paint layer active, unlocked, nothing selected."""
    t = tree()
    t.selection.clear()
    layer = t.nodes[PAINT]
    layer.lock_layer = False
    layer.lock_alpha = False
    layer.cache_enabled = False
    layer.cache_image = None
    select(PAINT)
    return paint_pixels()


def refusal(action, **kwargs):
    """The message `run_action` refuses *action* with, or None when it runs."""
    try:
        actions.run_action(bpy.context, action, **kwargs)
    except core.Refused as error:
        return str(error)
    return None


def test_clear_covers_the_whole_layer():
    section("no selection: the action covers the layer")
    if not available():
        return
    reset()
    check(bpy.ops.paint_system.clear_pixels() == {'FINISHED'}, "Clear reports finished")
    check(not read().any(), "every value is zero")


def test_invert_matches_the_byte_inversion():
    section("Invert Colors on a byte layer is exactly 255 - k")
    if not available():
        return
    before = as_bytes(reset()).reshape(-1, 4)
    bpy.ops.paint_system.invert_pixels()
    after = as_bytes(read()).reshape(-1, 4)
    check(np.array_equal(after[:, :3], 255 - before[:, :3]), "every colour byte is inverted")
    check(np.array_equal(after[:, 3], before[:, 3]), "alpha is left alone by default")

    before = as_bytes(reset()).reshape(-1, 4)
    bpy.ops.paint_system.invert_pixels(invert_r=False, invert_g=False, invert_b=False,
                                       invert_a=True)
    after = as_bytes(read()).reshape(-1, 4)
    check(np.array_equal(after[:, 3], 255 - before[:, 3]), "alpha inverts when it is asked for")
    check(np.array_equal(after[:, :3], before[:, :3]), "the colours are left alone")


def test_fill_stores_the_brush_color():
    section("Fill stores what a stroke of the current colour would store")
    if not available():
        return
    reset()
    owner = brush_color.color_owner(bpy.context)
    owner.color = (0.25, 0.5, 0.75)
    expected = brush_color.stored_fill_color(bpy.context, canvas())
    bpy.ops.paint_system.fill_pixels()
    got = read().reshape(-1, 4)
    worst = float(np.abs(as_bytes(got[:, :3]) - as_bytes(expected)).max())
    check(worst <= 1.0, f"the colour is the one a stroke would store, within {worst:.0f} byte")
    check(np.allclose(got[:, 3], 1.0, atol=1e-3), "Fill makes the layer opaque")

    reset()
    tree().nodes[PAINT].lock_alpha = True
    before = read().reshape(-1, 4)
    bpy.ops.paint_system.fill_pixels()
    after = read().reshape(-1, 4)
    check(np.array_equal(as_bytes(after[:, 3]), as_bytes(before[:, 3])),
          "Lock Alpha keeps the transparency a Fill found")


def test_selection_limits_the_action():
    section("a selection limits what an action reaches")
    if not available():
        return
    before = reset().reshape(SIZE, SIZE, 4)
    t = tree()
    t.selection.feather = 4.0
    t.selection.add_op('BOX', points=[(0.2, 0.3), (0.7, 0.8)])
    mask = raster.get_mask(t.selection, raster.image_size(canvas()))
    coverage = mask.read_bytes().astype(np.float64) / 255.0
    check(bpy.ops.paint_system.clear_pixels() == {'FINISHED'}, "Clear runs inside the selection")
    after = read().reshape(SIZE, SIZE, 4)

    outside = coverage <= 0.0
    check(outside.any() and np.array_equal(as_bytes(after[outside]), as_bytes(before[outside])),
          "texels the selection leaves out come back unchanged")
    inside = coverage >= 1.0
    check(inside.any() and not after[inside].any(), "texels it fully covers are erased")

    # The soft edge, by the same premultiplied mix in float64.
    edge = (coverage > 0.0) & (coverage < 1.0)
    m = coverage[edge][:, None]
    straight = before[edge].astype(np.float64)
    alpha = straight[:, 3:] * (1.0 - m)
    colour = np.where(alpha > 0.0,
                      straight[:, :3] * straight[:, 3:] * (1.0 - m) / np.maximum(alpha, 1e-12),
                      straight[:, :3] * (1.0 - m))
    expected = np.concatenate([colour, alpha], axis=1)
    worst = float(np.abs(as_bytes(after[edge]) - as_bytes(expected)).max())
    check(edge.any() and worst <= 1.0,
          f"the feathered edge matches the model within {worst:.0f} byte")
    t.selection.feather = 0.0


def test_a_selection_that_misses_the_layer_does_nothing():
    section("a selection that covers no texel of the layer")
    if not available():
        return
    before = reset()
    t = tree()
    # Outside the UV square, so the mask is live but covers nothing. The
    # session counts that as no selection, because painting through it is
    # harmless; erasing the whole layer would not be.
    t.selection.add_op('BOX', points=[(1.5, 1.5), (1.8, 1.8)])
    check(refusal(actions.CLEAR) == actions.NOTHING_COVERED,
          "Clear refuses by name instead of falling back to the whole layer")
    check(bpy.ops.paint_system.clear_pixels() == {'CANCELLED'}, "the operator cancels")
    check(np.array_equal(as_bytes(read()), as_bytes(before)), "not one value changed")


def test_undo_takes_one_step():
    section("one Ctrl+Z takes an action back")
    if not available():
        return
    before = as_bytes(reset())
    # The test fills the layer with a bare `foreach_set`, which belongs to
    # no undo step, and by now the image already has one, so `write_pixels`
    # pushes no baseline of its own. Record the state to come back to.
    undo_pixels.push_undo_step(canvas())
    bpy.ops.paint_system.invert_pixels()
    after = as_bytes(read())
    check(not np.array_equal(after, before), "the action changed the layer")

    bpy.ops.ed.undo()
    # The tree and image are fetched again by name: a Python reference to
    # an ID may dangle once the undo system has restored over it (PS-090).
    check(np.array_equal(as_bytes(read()), before), "one undo restores the pixels")
    bpy.ops.ed.redo()
    check(np.array_equal(as_bytes(read()), after), "one redo brings the action back")


def test_refusals():
    section("what an action will not do, it says")
    reset()
    t = tree()
    t.nodes[PAINT].lock_layer = True
    check(refusal(actions.CLEAR) == f"Layer '{PAINT}' is locked", "a locked layer is refused")
    check(not bpy.ops.paint_system.clear_pixels.poll(), "and the button is greyed out")
    t.nodes[PAINT].lock_layer = False

    t.nodes[PAINT].lock_alpha = True
    check(refusal(actions.CLEAR)
          == "Clear changes transparency, and this layer has Lock Alpha on",
          "Clear on a Lock Alpha layer is refused, because it could only do nothing")
    check(not bpy.ops.paint_system.clear_pixels.poll(), "and Clear is greyed out")
    if HAS_GPU:
        # The only check here that asks a poll to pass. Background 4.2 to
        # 5.1 have no GPU context to run a filter on, and every poll says
        # so before it looks at the layer.
        check(bpy.ops.paint_system.fill_pixels.poll(), "Fill stays available, honouring the lock")
    t.nodes[PAINT].lock_alpha = False

    select(SOLID)
    check(refusal(actions.FILL) == f"Layer '{SOLID}' has no image to edit",
          "a layer with no image is refused")
    check(not bpy.ops.paint_system.fill_pixels.poll(), "and the button is greyed out")
    select(PAINT)

    tiled = bpy.data.images.new("PS Action Tiled", 32, 32, tiled=True)
    t.nodes[PAINT].image, canvas_image = tiled, t.nodes[PAINT].image
    check(refusal(actions.INVERT) == "UDIM layers are not supported yet", "a UDIM layer is refused")
    t.nodes[PAINT].image = canvas_image
    bpy.data.images.remove(tiled)


def test_a_live_cache_refuses():
    section("a baked cache standing in for the layer")
    reset()
    t = tree()
    baked = bpy.data.images.new("PS Action Cache", 32, 32, alpha=True)
    layer = t.nodes[PAINT]
    layer.cache_image = baked
    layer.cache_enabled = True
    layer.cache_stale = False
    message = refusal(actions.INVERT)
    check(message == f"Layer '{PAINT}' shows its baked cache; turn Use Cache off to edit through it",
          f"an edit nobody would see is refused with the way out ({message})")
    layer.cache_enabled = False
    layer.cache_image = None
    bpy.data.images.remove(baked)


for test in (test_clear_covers_the_whole_layer,
             test_invert_matches_the_byte_inversion,
             test_fill_stores_the_brush_color,
             test_selection_limits_the_action,
             test_a_selection_that_misses_the_layer_does_nothing,
             test_undo_takes_one_step,
             test_refusals,
             test_a_live_cache_refuses):
    guarded(test)

# Give the GPU objects back while the context is still up; Python frees
# them at shutdown otherwise, which segfaults a background Blender.
core.release()
raster.release()

finish("SELECTION ACTIONS TEST")
