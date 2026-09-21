"""The painter's GPU passes, and a Painterly layer built with them (PS-053).

`filters.painter.plan` decides every stamp in numpy and has its own test,
`tests/test_painter_plan.py`. What is left for the GPU is checked here at
two levels. The passes run over textures the test builds, against numpy
written out here: the luma, the Sobel gradient with its edges clamped and
its y pointing up, the reduction to the strongest edge, and the gather
that reads both at every stamp centre. Then stamps are drawn one or two
at a time, which is where a turned quad or a blend order is easy to get
backwards and still look like paint.

Last, whole layers are built, and held to what has to be true of any
painting rather than to one: a flat picture paints to itself, a
transparent one stays transparent, the same settings paint the same
pixels twice, and the 4K build with the default settings stays inside
the ticket's ten seconds.

These need a GPU context, as `tests/test_filter_build.py` explains.
"""
import os
import sys
import time
import traceback
from math import pi

import bpy
import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (check, finish, import_from, register_addon,  # noqa: E402
                     section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
core = import_from("compiler.core")
filters_core = import_from("filters.core")
layer_build = import_from("filters.layer_build")
painter_build = import_from("filters.painter.build")
plan = import_from("filters.painter.plan")
create_managed_image = import_from("compiler.bake").create_managed_image

IMAGE = 'PaintSystemImageLayerNode'
FILTER = 'PaintSystemFilterLayerNode'

# The passes here run in RGBA32F, so only the shader's float arithmetic
# is in the way.
TOL = 1e-4
# A stamp is drawn into the pool's RGBA16F canvas from an R16F atlas.
HALF_TOL = 2e-3
BYTE_TOL = 2.0 / 255.0
SIZE = 1024
# The ticket's budget for a 4K build at the default settings, scaled as
# `tests/test_perf.py` scales its own.
BUDGET_4K = 10.0


def perf_scale():
    raw = os.environ.get("PS_PERF_SCALE", "").strip()
    if raw:
        return float(raw)
    return 5.0 if os.environ.get("CI") else 1.0


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def upload(values, texture_format='RGBA32F'):
    rows, cols = values.shape[:2]
    array = np.ascontiguousarray(values, dtype=np.float32)
    return gpu.types.GPUTexture(
        (cols, rows), format=texture_format,
        data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))


def run_spec(spec, values):
    """*values*, a ``(rows, cols, 4)`` array, through one pass of *spec*."""
    rows, cols = values.shape[:2]
    source = filters_core.PixelSource.from_texture(upload(values))
    try:
        framebuffer, _texture = filters_core.run_pass(spec, source)
    finally:
        source.release()
    return gpu_core.read_color(framebuffer, cols, rows)


def worst(got, want):
    return float(np.abs(np.asarray(got, np.float64) - np.asarray(want, np.float64)).max())


def sobel(luma):
    """v2's Sobel with its edge padding, on rows that run bottom-up."""
    p = np.pad(luma.astype(np.float64), 1, mode='edge')
    below, middle, above = p[:-2], p[1:-1], p[2:]
    gx = (below[:, 2:] + 2 * middle[:, 2:] + above[:, 2:]
          - below[:, :-2] - 2 * middle[:, :-2] - above[:, :-2])
    gy = (above[:, :-2] + 2 * above[:, 1:-1] + above[:, 2:]
          - below[:, :-2] - 2 * below[:, 1:-1] - below[:, 2:])
    return gx, gy


def canvas(side):
    texture = upload(np.zeros((side, side, 4), dtype=np.float32), 'RGBA16F')
    return texture, gpu.types.GPUFrameBuffer(color_slots=(texture,))


def draw(framebuffer, side, masks, stamps, size, cell):
    image, origins = plan.atlas(masks, cell, *plan.atlas_layout(len(masks), cell, 16384)[:2])
    atlas = painter_build._upload(image)
    painter_build._draw_stamps(framebuffer, (side, side), atlas,
                               plan.quads(stamps, size, origins, cell))
    return gpu_core.read_color(framebuffer, side, side)


def stamps_at(x, y, angle, colors):
    count = len(x)
    return plan.Stamps(x=np.asarray(x), y=np.asarray(y), brush=np.zeros(count, dtype=np.int64),
                       angle=np.asarray(angle, dtype=np.float64),
                       color=np.asarray(colors, dtype=np.float32))


def pixels(image):
    values = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(values)
    return values.reshape(image.size[1], image.size[0], 4)


def picture(side):
    """A smooth ramp with two hard-edged discs on it, opaque."""
    y, x = np.mgrid[0:side, 0:side] / side
    rgba = np.ones((side, side, 4), dtype=np.float32)
    rgba[..., 0] = x
    rgba[..., 1] = y
    rgba[..., 2] = 0.5
    for cx, cy, r, colour in ((0.3, 0.3, 0.18, (0.9, 0.2, 0.1)),
                              (0.7, 0.6, 0.22, (0.1, 0.3, 0.9))):
        rgba[(x - cx) ** 2 + (y - cy) ** 2 < r * r, :3] = colour
    return rgba


if available():
    try:
        rng = np.random.default_rng(3)

        section("the luma the direction is taken from")
        values = rng.random((24, 40, 4)).astype(np.float32)
        got = run_spec(painter_build.LUMA, values)
        luma = values[..., :3] @ np.array([0.2126, 0.7152, 0.0722])
        check(worst(got[..., 0], luma) < TOL and worst(got[..., 3], 1.0) == 0.0,
              "v2's weights, and opaque, so the blur after it is a plain one")

        section("the gradient")
        luma = rng.random((40, 56)).astype(np.float32)
        field = np.zeros((40, 56, 4), dtype=np.float32)
        field[..., 0] = luma
        got = run_spec(painter_build.SOBEL, field)
        gx, gy = sobel(luma)
        check(worst(got[..., 0], gx) < TOL and worst(got[..., 1], gy) < TOL,
              "the Sobel pass is v2's, edges clamped, with y pointing up the image")
        check(worst(got[..., 2], np.hypot(gx, gy)) < TOL, "and its magnitude beside it")

        y, x = np.mgrid[0:32, 0:32]
        ramp = np.zeros((32, 32, 4), dtype=np.float32)
        ramp[..., 0] = (x + y) / 64.0
        got = run_spec(painter_build.SOBEL, ramp)[8:24, 8:24]
        angle = np.arctan2(got[..., 1], got[..., 0])
        check(worst(angle, pi / 4) < 1e-4,
              "a picture brightening towards the upper right points its strokes at 45 degrees, "
              "which v2 turned to 135")

        section("the strongest edge")
        for shape, where in (((70, 100), (69, 99)), ((70, 100), (0, 0)),
                             ((9, 9), (8, 0)), ((1, 1), (0, 0))):
            field = np.zeros(shape + (4,), dtype=np.float32)
            field[..., 2] = rng.random(shape)
            field[where + (2,)] = 5.0
            got = painter_build._peak(upload(field))
            check(abs(got - 5.0) < 1e-6,
                  f"found by reduction over {shape[1]} by {shape[0]}, at {where} ({got})")

        section("reading the picture at the stamp centres")
        values = rng.random((48, 64, 4)).astype(np.float32)
        texture = upload(values)
        for count in (1, 1000, 1024):
            x = rng.integers(0, 64, count)
            y = rng.integers(0, 48, count)
            got = painter_build._gather((texture, texture), x, y)
            check(len(got) == 2 and got[0].shape == (count, 4)
                  and worst(got[0], values[y, x]) == 0.0 and worst(got[1], values[y, x]) == 0.0,
                  f"{count} centres read exactly, in the order they were drawn")
        corners = painter_build._gather((texture,), np.array([0, 63, 0, 63]),
                                        np.array([0, 0, 47, 47]))[0]
        check(worst(corners, values[[0, 0, 47, 47], [0, 63, 0, 63]]) == 0.0,
              "the corners of the picture included")

        section("one stamp")
        brush = rng.random((16, 16)).astype(np.float32)
        target, framebuffer = canvas(64)
        got = draw(framebuffer, 64, [brush], stamps_at([32], [32], [0.0], [(1.0, 1.0, 1.0, 1.0)]),
                   16, 16)
        check(worst(got[24:40, 24:40, 3], brush) < HALF_TOL,
              "unturned and at its own size, a stamp is its brush texel for texel")
        outside = got[..., 3].copy()
        outside[24:40, 24:40] = 0.0
        check(float(outside.max()) == 0.0, "and covers nothing past v2's square")

        bar = np.zeros((32, 32), dtype=np.float32)
        bar[14:18, :] = 1.0
        target, framebuffer = canvas(64)
        got = draw(framebuffer, 64, [bar], stamps_at([32], [32], [pi / 4], [(1.0, 1.0, 1.0, 1.0)]),
                   32, 32)[..., 3]
        check(got[40, 40] > 0.9 and got[24, 24] > 0.9 and got[23, 40] < 0.05
              and got[40, 23] < 0.05,
              "a bar turned by 45 degrees runs from the lower left to the upper right: "
              f"{got[40, 40]:.2f} and {got[24, 24]:.2f} on it, {got[23, 40]:.2f} "
              f"and {got[40, 23]:.2f} off it")

        solid = np.ones((8, 8), dtype=np.float32)
        target, framebuffer = canvas(32)
        got = draw(framebuffer, 32, [solid],
                   stamps_at([16, 16], [16, 16], [0.0, 0.0],
                             [(1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.5, 0.5)]), 8, 8)
        check(worst(got[16, 16], (0.5, 0.0, 0.5, 1.0)) < HALF_TOL,
              "a later stamp goes over an earlier one, premultiplied "
              f"({np.round(got[16, 16], 3).tolist()})")

        section("a Painterly layer")
        tree = bpy.data.node_groups.new("Painter", 'PaintSystemNodeTree')
        tree.initialize()
        source = create_managed_image("Painter Source", SIZE, SIZE)
        flat = np.tile(np.array([0.4, 0.6, 0.2, 1.0], dtype=np.float32), (SIZE, SIZE, 1))
        source.pixels.foreach_set(flat.ravel())
        source.update()
        with core.suspend_compile(tree):
            layer = tree.insert_layer_node(IMAGE)
            layer.image = source
            node = tree.insert_layer_node(FILTER)
            node.filter_type = 'PAINTERLY'
            node.resolution = str(SIZE)
        core.flush_now()

        labels = []
        run = layer_build.steps(bpy.context, tree, node)
        while True:
            try:
                labels.append(next(run))
            except StopIteration as done:
                built = done.value
                break
        fractions = [fraction for _label, fraction in labels]
        check(all(a <= b for a, b in zip(fractions, fractions[1:]))
              and 0.0 <= fractions[0] and fractions[-1] <= 1.0,
              f"the progress only moves forward ({len(labels)} units)")
        names = {label for label, _fraction in labels}
        check({"Painterly: planning the strokes", "Painterly: reading the picture",
               "Painterly: painting, step 1 of 4", "Painterly: painting, step 4 of 4",
               "Painterly: finishing"} <= names,
              "and says which step of the painting it is on")
        check(worst(pixels(built), flat) <= BYTE_TOL,
              "a flat picture paints to itself: every stroke picks up the colour it lands on")

        detailed = picture(SIZE)
        source.pixels.foreach_set(detailed.ravel())
        source.update()
        first = pixels(layer_build.build_layer(bpy.context, tree, node)).copy()
        uploaded = {key: entry[0]
                    for key, entry in painter_build._atlases[node.painter_brush].items()}
        second = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(bool(np.array_equal(first, second)), "the same settings paint the same pixels twice")
        kept = painter_build._atlases[node.painter_brush]
        check(uploaded and all(kept[key][0] is texture for key, texture in uploaded.items()),
              f"the second time with the {len(uploaded)} atlases the first one uploaded")
        moved = np.abs(first - detailed).max(axis=2) > BYTE_TOL
        check(float(moved.mean()) > 0.05,
              f"and they are painted: {moved.mean():.0%} of the texels moved off the picture")

        node.painter_seed = 7
        core.flush_now()
        reseeded = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(not np.array_equal(reseeded, first), "another seed paints other strokes")
        node.painter_seed = 42

        # The ramp's gradient is a small fraction of the discs' edges, so a
        # threshold between the two keeps only the strokes on the discs.
        # Nothing kept at all would pass a peak read as infinite, which is
        # why the lower bound is there.
        node.painter_edge_threshold = 30.0
        core.flush_now()
        edged = pixels(layer_build.build_layer(bpy.context, tree, node))
        few = np.abs(edged - detailed).max(axis=2) > BYTE_TOL
        check(0.0 < float(few.mean()) < float(moved.mean()) / 2,
              f"a threshold leaves out the strokes off the strong edges "
              f"({few.mean():.1%} moved, {moved.mean():.1%} without it)")
        node.painter_edge_threshold = 0.0

        clear = np.zeros((SIZE, SIZE, 4), dtype=np.float32)
        source.pixels.foreach_set(clear.ravel())
        source.update()
        core.flush_now()
        empty = pixels(layer_build.build_layer(bpy.context, tree, node))
        check(float(empty[..., 3].max()) == 0.0,
              "a transparent stack stays transparent: no stroke is placed where there is nothing")

        section("at 4K")
        source.pixels.foreach_set(picture(SIZE).ravel())
        source.update()
        node.resolution = '4096'
        core.flush_now()
        start = time.perf_counter()
        layer_build.build_layer(bpy.context, tree, node)
        took = time.perf_counter() - start
        limit = BUDGET_4K * perf_scale()
        check(took < limit, f"the default settings paint in {took:.2f} s (limit {limit:.0f} s)")

    except Exception:
        traceback.print_exc()
        check(False, "unexpected exception")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender. The textures made above are
# module globals, so they go too.
target = framebuffer = texture = uploaded = kept = None
import_from("filters.composite").release()
import_from("filters.blend_glsl").release()
filters_core.release()
painter_build.release()

finish("FILTER PAINTER TEST")
