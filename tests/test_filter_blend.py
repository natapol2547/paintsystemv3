"""The GPU blend pass agrees with the render, mode by mode (PS-057).

`filters.blend_glsl` composites a layer over the stack below it so that a
filter layer can be handed the same picture the render engines produce.
"Same" is the whole claim, so every case here is checked against a real
Cycles bake of `compiler.library.layer_blend_group` for the same mode and
the same inputs, not against a formula written out twice.

The modes that pass are what `ALLOWED_BLEND_MODES` may contain. A mode
that fails is not a bug in the filter layer: it falls back to baking its
input with Cycles. The list is asserted against the modes that actually
agree, in both directions, so it cannot quietly drift.

These need a GPU context. Blender 5.2 added `gpu.init()`, which builds
one in background mode, so they run in the ordinary headless job there.
Background 4.2 to 5.1 have no way to get one and skip; that coverage
comes from the windowed job.
"""
import os
import sys

import gpu
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (bake_group, check, finish, fmt, import_from, pixel_at,  # noqa: E402
                     register_addon, section, skip)

register_addon()
gpu_core = import_from("gpu_passes.core")
blend_glsl = import_from("filters.blend_glsl")
library = import_from("compiler.library")

# 1/255 is the resolution a byte layer has anyway, and the two sides run
# on different hardware: Cycles on the CPU, the pass on whatever GPU is
# here.
TOL = 1.5 / 255.0
SIZE = 4

# Chosen as in tests/test_blend.py: no mode goes negative, and DIVIDE,
# DODGE and BURN stay away from their divide-by-zero branches.
CB = (0.8, 0.7, 0.6)
CS = (0.3, 0.4, 0.25)

CASES = [
    # (label, backdrop alpha, source alpha, opacity, clip)
    ("over an opaque backdrop", 1.0, 1.0, 1.0, False),
    ("at partial opacity", 1.0, 1.0, 0.4, False),
    ("over a half-transparent backdrop", 0.5, 1.0, 1.0, False),
    ("with a half-transparent source", 0.5, 0.5, 1.0, False),
    ("clipped to a half-transparent backdrop", 0.5, 1.0, 1.0, True),
    ("clipped at partial opacity", 0.5, 1.0, 0.4, True),
    ("over a transparent backdrop", 0.0, 1.0, 1.0, False),
]


def available():
    if gpu_core.gpu_available():
        return True
    skip("no GPU context in this session; gpu.init() arrived in Blender 5.2")
    return False


def constant_texture(rgba):
    """A SIZE x SIZE RGBA16F texture of one colour."""
    values = np.tile(np.asarray(rgba, dtype=np.float32), SIZE * SIZE)
    return gpu.types.GPUTexture(
        (SIZE, SIZE), format='RGBA16F',
        data=gpu.types.Buffer('FLOAT', values.size, values))


def blend_pixel(mode, backdrop, source, opacity, clip):
    target = gpu.types.GPUTexture((SIZE, SIZE), format='RGBA16F')
    framebuffer = blend_glsl.blend_over(
        constant_texture(backdrop), constant_texture(source), target, (SIZE, SIZE),
        mode=mode, opacity=opacity, clip=clip)
    values = gpu_core.read_color(framebuffer, SIZE, SIZE)
    return tuple(float(v) for v in values[SIZE // 2, SIZE // 2])


def baked_pixel(mode, backdrop, source, opacity, clip):
    rgba = bake_group(library.layer_blend_group(mode), color="Color", alpha="Alpha",
                      size=SIZE, inputs={
                          "Prev Color": backdrop[:3] + (1.0,),
                          "Prev Alpha": backdrop[3],
                          "Color": source[:3] + (1.0,),
                          "Alpha": source[3],
                          "Opacity": opacity,
                          "Clip": 1.0 if clip else 0.0,
                      })
    return pixel_at(rgba, 0.5, 0.5, SIZE)


if available():
    agreeing = set()
    for mode in sorted(blend_glsl.BLEND_MODE_IDS):
        section(mode)
        agrees = True
        for label, backdrop_alpha, source_alpha, opacity, clip in CASES:
            backdrop = CB + (backdrop_alpha,)
            source = CS + (source_alpha,)
            got = blend_pixel(mode, backdrop, source, opacity, clip)
            want = baked_pixel(mode, backdrop, source, opacity, clip)
            # A transparent result carries no colour on either side, so
            # only its alpha means anything there.
            channels = [3] if want[3] <= TOL else [0, 1, 2, 3]
            close = all(abs(got[i] - want[i]) <= TOL for i in channels)
            agrees = agrees and close
            check(close, f"{mode} {label}: {fmt(got)} baked {fmt(want)}")
        if agrees:
            agreeing.add(mode)

    section("the allow-list")
    allowed = set(blend_glsl.ALLOWED_BLEND_MODES)
    check(allowed <= agreeing,
          f"every allowed mode agrees with the bake; wrong: {sorted(allowed - agreeing)}")
    check(agreeing <= allowed,
          f"every agreeing mode is allowed; missing: {sorted(agreeing - allowed)}")
    check(allowed <= set(blend_glsl.BLEND_MODE_IDS),
          "the allow-list names modes the shader knows")

    section("release")
    blend_glsl.release()
    check(blend_glsl._shader is None and blend_glsl._no_mask_texture is None,
          "release drops the shader and its placeholder")

# Python's own teardown would free them after the GPU context has gone,
# which segfaults a background Blender.
blend_glsl.release()

finish("FILTER BLEND TEST")
