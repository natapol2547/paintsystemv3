"""Where the painter's stamps go, checked without a GPU (PS-053).

`filters.painter.plan` is plain numpy, so it is held to v2's arithmetic
directly: the schedule and the stamp count are compared with v2's own
code, written out below as it stands in v2's `brush_painter_core`, on
brushes read the way v2 read them. The rest is what v3 changed on
purpose, and what a later change could quietly undo:

- the random numbers come from the seed alone, per step and per stream,
  so raising the coverage adds stamps without moving one, and a stamp
  dropped over a transparent picture shifts nothing after it;
- the stroke follows the gradient on every edge, the diagonals included,
  which v2 mirrored;
- a stamp's quad sits on texel edges when it is not turned, which is what
  lets a stamp at angle zero reproduce its brush.

`tests/test_filter_painter.py` checks the GPU half.
"""
import colorsys
import os
import sys
import traceback
from dataclasses import replace
from math import pi, tau

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, finish, import_from, register_addon, section  # noqa: E402

register_addon()
plan = import_from("filters.painter.plan")
brushes = import_from("filters.painter.brushes")
layer_specs = import_from("filters.layer_specs")
filters_core = import_from("filters.core")

FILTER = 'PaintSystemFilterLayerNode'
PRESET_FOLDER = os.path.join(os.path.dirname(brushes.__file__), "presets")


# -- v2, as it stands ---------------------------------------------------------


def v2_resize(mask, out_h, out_w):
    """v2's `_resize_mask_bilinear`."""
    src_h, src_w = mask.shape
    if src_h == out_h and src_w == out_w:
        return mask.astype(np.float32, copy=True)
    if out_h <= 1 or out_w <= 1:
        return np.full((max(1, out_h), max(1, out_w)), float(mask.mean()), dtype=np.float32)
    y = np.linspace(0, src_h - 1, out_h, dtype=np.float32)
    x = np.linspace(0, src_w - 1, out_w, dtype=np.float32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    y1 = np.minimum(y0 + 1, src_h - 1)
    x1 = np.minimum(x0 + 1, src_w - 1)
    wy = y - y0
    wx = x - x0
    ia = mask[y0[:, None], x0[None, :]]
    ib = mask[y0[:, None], x1[None, :]]
    ic = mask[y1[:, None], x0[None, :]]
    id_ = mask[y1[:, None], x1[None, :]]
    wa = (1.0 - wy)[:, None] * (1.0 - wx)[None, :]
    wb = (1.0 - wy)[:, None] * wx[None, :]
    wc = wy[:, None] * (1.0 - wx)[None, :]
    wd = wy[:, None] * wx[None, :]
    return (ia * wa + ib * wb + ic * wc + id_ * wd).astype(np.float32, copy=False)


def v2_count(density, brush_list, H, W):
    """v2's `calculate_brush_area_density`, on brushes already resized."""
    total_brush_area = 0
    for brush in brush_list:
        total_brush_area += np.sum(brush > 0)
    avg_brush_area = total_brush_area / len(brush_list)
    image_area = H * W
    num_samples = int(image_area * density / (avg_brush_area * 0.7))
    return max(50, min(num_samples, image_area // 8))


def v2_steps(settings, brush_list, H, W):
    """v2's `precalculate_step_data`, as ``(size, opacity, count)`` per step."""
    out = []
    for step in range(settings.steps):
        if settings.steps == 1:
            scale = settings.min_scale
            opacity = settings.end_opacity
        else:
            scale = settings.max_scale + (settings.min_scale - settings.max_scale) * step / (
                settings.steps - 1)
            opacity = settings.start_opacity + (
                settings.end_opacity - settings.start_opacity) * step / (settings.steps - 1)
        size = max(1, int(scale * min(H, W)))
        scaled = [v2_resize(brush, size, size) for brush in brush_list]
        out.append((size, opacity, v2_count(settings.density, scaled, H, W)))
    return out


def v2_brush(path):
    """v2's `load_brush_texture`: top-down rows, alpha, padded to a square."""
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        channels = image.channels
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    pixels = np.flipud(pixels.reshape(height, width, channels))
    mask = np.clip(pixels[..., 3], 0.0, 1.0).astype(np.float32)
    h, w = mask.shape
    if h != w:
        side = max(h, w)
        square = np.zeros((side, side), dtype=np.float32)
        top, left = (side - h) // 2, (side - w) // 2
        square[top:top + h, left:left + w] = mask
        mask = square
    return mask


def v2_brushes(folder):
    folder = os.path.join(PRESET_FOLDER, folder)
    return [v2_brush(os.path.join(folder, entry)) for entry in sorted(os.listdir(folder))]


# -- helpers ------------------------------------------------------------------


def same(a, b):
    return a.shape == b.shape and bool(np.array_equal(a, b))


def planned(settings, step, drawn, colors, gradients, peak=None):
    return plan.stamps(settings, step, drawn, colors, gradients, peak)


try:
    section("the brushes")
    names = [item[0] for item in brushes.brush_items()]
    check(names == ['GOUACHE_SHORT_1', 'GOUACHE_SHORT_2', 'CIRCLE'],
          f"the two presets v2 shipped and the circle are offered ({names})")
    images_before = len(bpy.data.images)
    short_1 = brushes.masks('GOUACHE_SHORT_1')
    short_2 = brushes.masks('GOUACHE_SHORT_2')
    check(len(short_1) == 16 and len(short_2) == 5,
          f"every image of each preset is a brush ({len(short_1)} and {len(short_2)})")
    check(all(mask.ndim == 2 and mask.shape[0] == mask.shape[1] and mask.dtype == np.float32
              and float(mask.min()) >= 0.0 and float(mask.max()) <= 1.0
              for mask in short_1 + short_2),
          "each one a square float mask in [0, 1]")
    check(len(bpy.data.images) == images_before,
          "and reading them leaves no image datablock behind")
    check(brushes.masks('GOUACHE_SHORT_1') is short_1,
          "a preset is read once and kept, rather than on every rebuild")
    circle = brushes.masks('CIRCLE')
    check(len(circle) == 1 and circle[0].shape == (50, 50),
          "the circle is v2's, fifty texels across")
    try:
        brushes.masks('NO_SUCH_BRUSH')
        refused = ""
    except filters_core.Refused as error:
        refused = str(error)
    check("NO_SUCH_BRUSH" in refused, f"a brush that does not exist is refused by name ({refused})")

    # The first image of the first preset is taller than wide, so it is
    # padded left and right; flipping it and padding the top-down copy
    # as v2 did has to give the same brush, the right way up.
    folder = os.path.join(PRESET_FOLDER, "gouache_short_1")
    first = sorted(os.listdir(folder))[0]
    check(same(short_1[0], np.flipud(v2_brush(os.path.join(folder, first)))),
          "a preset brush is v2's brush, with its rows bottom-up")
    odd = np.ones((3, 6), dtype=np.float32)
    padded = plan.square(odd)
    check(same(np.flipud(padded)[1:4], odd) and float(np.flipud(padded)[0].sum()) == 0.0,
          "an odd row of padding lands where v2's did once the rows are flipped back")

    section("resizing a brush")
    rng = np.random.default_rng(7)
    worst = 0.0
    for side, target in ((50, 17), (107, 64), (212, 3), (30, 90), (1024, 204)):
        mask = rng.random((side, side)).astype(np.float32)
        worst = max(worst, float(np.abs(plan.resize_bilinear(mask, target)
                                        - v2_resize(mask, target, target)).max()))
    check(worst < 1e-6, f"the resize the count is taken on is v2's (worst {worst:.2e})")
    check(plan.resize_bilinear(np.eye(4, dtype=np.float32), 1).shape == (1, 1),
          "and a brush resized to one texel is its mean, as in v2")

    mask = rng.random((64, 64)).astype(np.float32)
    boxed = mask.reshape(16, 4, 16, 4).mean(axis=(1, 3))
    check(np.abs(plan.resize(mask, 16) - boxed).max() < 1e-6,
          "shrinking by a whole factor averages every texel rather than skipping most")
    # v2's circle is half a texel off centre itself, so the brush for
    # this one is symmetric by construction.
    fifty = rng.random((50, 50)).astype(np.float32)
    fifty = fifty + fifty[::-1]
    fifty = fifty + fifty[:, ::-1]
    small = plan.resize(fifty, 12)
    check(np.abs(small - small[::-1]).max() < 1e-6 and np.abs(small - small[:, ::-1]).max() < 1e-6,
          "and the rows that do not divide are trimmed evenly, so the brush stays centred")
    # Within single precision: `resize` runs the bilinear step one axis at
    # a time, which rounds differently.
    check(np.abs(plan.resize(fifty, 40) - plan.resize_bilinear(fifty, 40)).max() < 1e-6,
          "shrinking by less than two is the plain bilinear resize")
    worst = 0.0
    for side, target in ((50, 17), (107, 64), (30, 90), (1024, 409), (312, 311)):
        mask = rng.random((side, side)).astype(np.float32)
        factor = side // target
        if factor >= 2:
            kept = side // factor * factor
            start = (side - kept) // 2
            cells = kept // factor
            reference = mask[start:start + kept, start:start + kept].reshape(
                cells, factor, cells, factor).mean(axis=(1, 3))
        else:
            reference = mask
        worst = max(worst, float(np.abs(plan.resize(mask, target)
                                        - plan.resize_bilinear(reference, target)).max()))
    check(worst < 1e-6, f"at any factor (worst {worst:.2e}), well inside a half float's step")

    section("the covered area, counted without resizing")
    presets = [mask for name in ('GOUACHE_SHORT_1', 'GOUACHE_SHORT_2', 'CIRCLE')
               for mask in brushes.masks(name)]
    sides = list(range(1, 65)) + [int(side) for side in rng.integers(65, 1100, 24)]
    misses = []
    for mask in presets:
        inside = plan.covered(mask)
        for side in sides:
            want = int(np.count_nonzero(plan.resize_bilinear(mask, side) > 0))
            got = plan.covered_area(mask, side, inside)
            if got != want:
                misses.append((mask.shape[0], side, got, want))
    check(all(plan.covered(mask) is not None for mask in presets) and not misses,
          f"every shipped brush at {len(sides)} sides counts what the resize leaves "
          f"({len(presets) * len(sides)} pairs; first miss {misses[:1]})")
    sparse = np.zeros((40, 40), dtype=np.float32)
    sparse[::7, ::5] = 1.0
    sparse[13, 21] = 1e-3
    check(all(plan.covered_area(sparse, side, plan.covered(sparse))
              == np.count_nonzero(plan.resize_bilinear(sparse, side) > 0) for side in range(1, 90)),
          "and so does a brush of isolated texels, where every tap it skips shows")
    faint = sparse.copy()
    faint[0, 0] = 1e-30
    check(plan.covered(faint) is None and plan.covered(-sparse) is None
          and plan.covered(np.full((4, 4), np.nan, dtype=np.float32)) is None,
          "a value the resize could round away, a negative one or NaN falls back to resizing")
    check(plan.covered_area(faint, 9, None)
          == np.count_nonzero(plan.resize_bilinear(faint, 9) > 0),
          "which counts the resize itself")
    areas = plan.Areas(brushes.masks('CIRCLE'))
    check(areas.mean(12) == areas.mean(12) == plan.covered_area(
              brushes.masks('CIRCLE')[0], 12, plan.covered(brushes.masks('CIRCLE')[0])),
          "Areas averages the brushes' counts and keeps each side's")

    section("the schedule, against v2")
    worst_case = ""
    matched = 0
    for preset, folder in (('GOUACHE_SHORT_1', "gouache_short_1"),
                           ('GOUACHE_SHORT_2', "gouache_short_2"), ('CIRCLE', None)):
        v2_masks = v2_brushes(folder) if folder else [plan.circle()]
        ours = brushes.areas(preset)
        for settings in (plan.Settings(), plan.Settings(steps=1), plan.Settings(steps=7),
                         plan.Settings(density=0.25, min_scale=0.01, max_scale=0.4, steps=5)):
            for width, height in ((1024, 1024), (2048, 2048), (4096, 4096), (2048, 1024)):
                want = v2_steps(settings, v2_masks, height, width)
                got = [(step.size, step.opacity, step.count)
                       for step in plan.schedule(settings, width, height, ours)]
                if got == want:
                    matched += 1
                elif not worst_case:
                    worst_case = f"{preset} {width}x{height} {settings}: {got} != {want}"
    check(not worst_case, f"sizes, opacities and counts are v2's exactly ({matched} schedules)"
          + (f"; first miss {worst_case}" if worst_case else ""))
    circle_areas = brushes.areas('CIRCLE')
    steps = plan.schedule(plan.Settings(), 2048, 2048, circle_areas)
    check([step.index for step in steps] == [0, 1, 2, 3]
          and steps[0].size > steps[-1].size and steps[0].opacity < steps[-1].opacity,
          "from the largest brush to the smallest, and from First Opacity to Last")
    tiny = plan.schedule(plan.Settings(min_scale=0.001, max_scale=0.001), 64, 64, circle_areas)
    check(all(step.size == 1 and step.count == 64 * 64 // 8 for step in tiny),
          "a brush smaller than a texel is one texel, at most one stamp per eight texels")
    empty = plan.Areas([np.zeros((8, 8), dtype=np.float32)])
    check(plan.stamp_count(0.7, 512, 512, empty.mean(8)) == 512 * 512 // 8,
          "and a brush that covers nothing counts as one texel instead of dividing by zero")

    section("the random numbers")
    settings = plan.Settings()
    step = plan.schedule(settings, 512, 512, circle_areas)[0]
    drawn = plan.draws(settings, step, 512, 512, 3)
    again = plan.draws(settings, step, 512, 512, 3)
    streams = ('x', 'y', 'brush', 'turn', 'jitter')
    check(all(same(getattr(drawn, name), getattr(again, name)) for name in streams),
          "the same seed draws the same numbers")
    check(len(drawn.x) == step.count and drawn.jitter.shape == (step.count, 3),
          "one of each per stamp, three colour shifts each")
    check(0 <= drawn.x.min() and drawn.x.max() < 512 and 0 <= drawn.brush.min()
          and drawn.brush.max() < 3 and -0.5 <= drawn.turn.min() and drawn.turn.max() < 0.5,
          "centres on the image, a brush that exists, and turns in [-0.5, 0.5)")
    other = plan.draws(replace(settings, seed=43), step, 512, 512, 3)
    check(not same(other.x, drawn.x), "another seed draws other numbers")
    later = plan.draws(settings, replace(step, index=1), 512, 512, 3)
    check(not same(later.x, drawn.x), "and so does another step, so the steps do not repeat")

    denser_step = replace(step, count=step.count * 3)
    denser = plan.draws(settings, denser_step, 512, 512, 3)
    count = step.count
    check(all(same(getattr(denser, name)[:count], getattr(drawn, name)) for name in streams),
          "more stamps extend every stream, so raising the coverage moves no stamp")
    turned = plan.draws(replace(settings, random_rotation=True, hue=0.5), step, 512, 512, 3)
    check(all(same(getattr(turned, name), getattr(drawn, name)) for name in streams),
          "and what the settings use of them changes none of them")

    section("which stamps land")
    colors = np.tile(np.array([0.2, 0.4, 0.6, 1.0], dtype=np.float32), (count, 1))
    gradients = np.tile(np.array([1.0, 0.0, 1.0], dtype=np.float32), (count, 1))
    everything = planned(settings, step, drawn, colors, gradients)
    check(len(everything) == count, "over an opaque picture every stamp lands")
    holes = colors.copy()
    holes[::3, 3] = 0.0
    some = planned(settings, step, drawn, holes, gradients)
    kept = np.arange(count) % 3 != 0
    check(len(some) == int(kept.sum()), "where the picture is transparent no stamp is placed")
    check(same(some.x, everything.x[kept]) and same(some.y, everything.y[kept])
          and same(some.brush, everything.brush[kept]) and same(some.angle, everything.angle[kept]),
          "and dropping those changes nothing about the rest")

    magnitudes = np.array([0.1, 0.5, 1.0, 0.49], dtype=np.float32)
    few = plan.Step(index=0, size=8, opacity=1.0, count=4)
    few_drawn = plan.draws(settings, few, 64, 64, 1)
    few_colors = np.ones((4, 4), dtype=np.float32)
    few_gradients = np.stack([magnitudes, np.zeros(4, np.float32), magnitudes], axis=1)
    edgy = replace(settings, threshold=0.5)
    got = planned(edgy, few, few_drawn, few_colors, few_gradients, peak=1.0)
    check(same(got.x, few_drawn.x[[1, 2]]),
          "a threshold keeps the stamps on edges at least that strong, relative to the strongest")
    got = planned(edgy, few, few_drawn, few_colors, few_gradients * 2.0, peak=2.0)
    check(same(got.x, few_drawn.x[[1, 2]]), "relative, so the scale of the gradient does not matter")
    check(len(planned(edgy, few, few_drawn, few_colors, few_gradients, peak=0.0)) == 0
          and len(planned(edgy, few, few_drawn, few_colors, few_gradients, peak=None)) == 0,
          "a picture with no edges at all keeps nothing once there is a threshold")
    check(len(planned(settings, few, few_drawn, few_colors, few_gradients, peak=None)) == 4,
          "and with no threshold the peak is not even needed")

    section("the colour of a stamp")
    faded = replace(few, opacity=0.5)
    half = np.array([[0.2, 0.4, 0.8, 0.5]] * 4, dtype=np.float32)
    got = planned(settings, faded, few_drawn, half, few_gradients)
    check(np.abs(got.color[0] - (0.05, 0.1, 0.2, 0.25)).max() < 1e-6,
          f"premultiplied by its own alpha and the step's opacity ({got.color[0]})")
    check(plan.jitter_hsv(half, (0.0, 0.0, 0.0), few_drawn.jitter) is half,
          "with no colour variation the colour is left exactly as sampled")

    samples = np.concatenate([rng.random((200, 3)), np.eye(3), 1.0 - np.eye(3),
                              [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5], [1.0, 1.0, 1.0]]])
    h, s, v = plan.rgb_to_hsv(samples)
    want = np.array([colorsys.rgb_to_hsv(*rgb) for rgb in samples])
    check(np.abs(np.stack([h, s, v], axis=1) - want).max() < 1e-9,
          "hue, saturation and value are the standard ones, primaries and greys included")
    back = plan.hsv_to_rgb(*want.T)
    check(np.abs(back - samples).max() < 1e-9, "and convert back to the colour they came from")

    rgba = np.concatenate([samples, np.full((len(samples), 1), 0.7)], axis=1)
    jitter = rng.random((len(samples), 3)) - 0.5
    shifted = plan.jitter_hsv(rgba, (1.0, 0.0, 0.0), jitter)
    h2, _s, _v = plan.rgb_to_hsv(shifted[:, :3])
    coloured = (s > 1e-3) & (v > 1e-3)
    miss = np.abs(((h2 - (h + jitter[:, 0])) + 0.5) % 1.0 - 0.5)
    check(float(miss[coloured].max()) < 1e-6 and bool(np.all(shifted[:, 3] == 0.7)),
          "a hue shift of one turns each hue by its own draw, up to half a turn either way, "
          "and leaves alpha alone")
    shifted = plan.jitter_hsv(rgba, (0.0, 0.4, 0.4), jitter)
    _h, s2, v2 = plan.rgb_to_hsv(shifted[:, :3])
    want_v = np.clip(v + jitter[:, 2] * 0.4, 0.0, 1.0)
    want_s = np.clip(s + jitter[:, 1] * 0.4, 0.0, 1.0)
    lit = want_v > 1e-3
    check(float(np.abs(v2 - want_v).max()) < 1e-6
          and float(np.abs(s2 - want_s)[lit].max()) < 1e-6,
          "saturation and value by up to a fifth either way at 0.4, clamped to a real colour")

    section("which way a stroke turns")
    # Rows run bottom-up, so a gradient of (1, 1) points up and to the
    # right, and the stroke turns counter-clockwise to follow it. v2 took
    # its angle from rows that ran top-down and turned the diagonals the
    # other way; this is the v3 fix.
    diagonals = np.array([[1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0], [1.0, -1.0], [0.0, 1.0]],
                         dtype=np.float32)
    turns = plan.Step(index=0, size=8, opacity=1.0, count=5)
    turn_drawn = plan.draws(settings, turns, 64, 64, 1)
    turn_colors = np.ones((5, 4), dtype=np.float32)
    turn_gradients = np.concatenate([diagonals, np.ones((5, 1), np.float32)], axis=1)
    got = planned(settings, turns, turn_drawn, turn_colors, turn_gradients).angle
    want = np.array([pi / 4, 3 * pi / 4, -3 * pi / 4, -pi / 4, pi / 2])
    check(np.abs(got - want).max() < 1e-6, f"each diagonal gets its own angle ({np.round(got, 4)})")
    offset = replace(settings, rotation=pi / 6)
    got = planned(offset, turns, turn_drawn, turn_colors, turn_gradients).angle
    check(np.abs(got - want - pi / 6).max() < 1e-6, "Rotation turns every stroke by the same amount")
    loose = replace(settings, random_rotation=True, rotation_range=pi / 2)
    got = planned(loose, turns, turn_drawn, turn_colors, turn_gradients).angle
    check(np.abs(got - want - turn_drawn.turn * pi / 2).max() < 1e-6
          and float(np.abs(got - want).max()) <= pi / 4,
          "and Random Rotation by up to half the range either way, from the seeded stream")

    section("the atlas")
    check(plan.atlas_layout(1, 50, 16384) == (1, 1, 50), "one brush is one cell")
    check(plan.atlas_layout(16, 300, 16384) == (4, 4, 300), "sixteen are four by four")
    check(plan.atlas_layout(5, 600, 16384) == (3, 2, 600), "five are three by two")
    columns, rows, cell = plan.atlas_layout(16, 1000, 2048)
    check(cell == 510 and columns * (cell + 2) <= 2048,
          f"too large for the texture, the cells shrink to fit, gutters included ({cell})")
    constants = [np.full((8, 8), value, dtype=np.float32) for value in (0.25, 0.5, 0.75)]
    image, origins = plan.atlas(constants, 4, 2, 2)
    check(image.shape == (12, 12) and same(origins, np.array([[1, 1], [7, 1], [1, 7]],
                                                             dtype=np.float32)),
          f"cells of four with a texel of gutter all round ({origins.tolist()})")
    inside = np.zeros(image.shape, dtype=bool)
    for (x, y), value in zip(origins.astype(int), (0.25, 0.5, 0.75)):
        inside[y:y + 4, x:x + 4] = True
        check(bool(np.all(image[y:y + 4, x:x + 4] == value)), f"brush {value} fills its own cell")
    check(float(np.abs(image[~inside]).max()) == 0.0,
          "and every gutter texel is empty, so a bilinear read at a cell edge fades out")

    section("the quads")
    for size in (8, 7):
        single = plan.Stamps(x=np.array([10]), y=np.array([20]), brush=np.array([0]),
                             angle=np.array([0.0]),
                             color=np.array([[1.0, 1.0, 1.0, 1.0]], dtype=np.float32))
        positions, coords, colours, indices = plan.quads(single, size, np.array([[1.0, 1.0]]), 5)
        low = (10 - size // 2, 20 - size // 2)
        check(same(positions, np.array([low, (low[0] + size, low[1]),
                                        (low[0] + size, low[1] + size),
                                        (low[0], low[1] + size)], dtype=np.float32)),
              f"unturned, a stamp of {size} covers v2's square, corners on texel edges")
        check(same(coords, np.array([(1, 1), (6, 1), (6, 6), (1, 6)], dtype=np.float32)),
              "and maps its brush's whole cell onto it")
    check(same(indices, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32))
          and colours.shape == (4, 4), "two triangles and a colour per corner")
    quarter = replace(single, angle=np.array([pi / 2]))
    positions = plan.quads(quarter, 8, np.array([[1.0, 1.0]]), 5)[0]
    check(np.abs(positions[0] - (14.0, 16.0)).max() < 1e-5,
          f"a quarter turn is counter-clockwise: the lower-left corner goes to the lower right "
          f"({positions[0].tolist()})")
    pair = plan.Stamps(x=np.array([0, 5]), y=np.array([0, 5]), brush=np.array([1, 0]),
                       angle=np.zeros(2), color=np.ones((2, 4), dtype=np.float32))
    positions, coords, colours, indices = plan.quads(pair, 4, np.array([[1.0, 1.0], [7.0, 1.0]]), 4)
    check(same(indices[2:], np.array([[4, 5, 6], [4, 6, 7]], dtype=np.int32))
          and same(coords[0], np.array([7.0, 1.0], dtype=np.float32)),
          "each stamp gets its own four corners and its own brush's cell")

    section("the layer's settings")
    tree = bpy.data.node_groups.new("Painter Plan", 'PaintSystemNodeTree')
    tree.initialize()
    node = tree.insert_layer_node(FILTER)
    node.filter_type = 'PAINTERLY'
    defaults = plan.Settings.of(node)
    # Float properties hold single precision, so compare what they hold.
    want = {key: float(np.float32(value)) if isinstance(value, float) else value
            for key, value in plan.Settings().as_dict().items()}
    check(defaults.as_dict() == want,
          "a new Painterly layer starts at the planner's defaults")
    kind = layer_specs.LAYER_FILTERS['PAINTERLY']
    stamp = kind.fingerprint(node)
    node.painter_seed = 7
    check(kind.fingerprint(node) != stamp, "a build records the seed it painted with")
    node.painter_seed = 42
    node.painter_brush = 'CIRCLE'
    check(kind.fingerprint(node) != stamp, "and the brush")
    node.painter_brush = 'GOUACHE_SHORT_1'
    check(kind.fingerprint(node) == stamp, "and nothing else when both are put back")
    check(abs(node.painter_rotation_range - tau) < 1e-6, "Rotation Range starts at a full turn")
    bpy.data.node_groups.remove(tree)

except Exception:
    traceback.print_exc()
    check(False, "unexpected exception")

brushes.release()

finish("PAINTER PLAN TEST")
