# SPDX-License-Identifier: GPL-3.0-or-later
"""The painter's build: GPU passes around the numpy planning (PS-053).

`build` is the Painterly kind's build hook, which `filters.layer_build`
runs in place of a list of passes. In order:

1. the stack below is encoded to sRGB, because v2 painted stored byte
   values and its look depends on it -- the blur, the luma the gradient
   is taken on, and the edge of every "over";
2. the colour the stamps pick up is blurred with v2's kernel, and a Sobel
   pass over the blurred luma gives the gradient field, whose peak is
   found by reduction when the threshold needs it;
3. one small pass reads both at every stamp centre `plan.draws` picked,
   which is the only readback before the result;
4. `plan.stamps` decides which land and how, and each step's stamps are
   drawn as turned quads into a premultiplied copy of the picture, from
   one atlas of that step's brushes;
5. the canvas is un-premultiplied and decoded back to scene linear,
   because the build encodes whatever a kind returns.

The textures in flight are the pool's, the full size of the layer; the
small ones -- positions, gathered values, the reduction, the atlas --
are made here and dropped with the generator's frame.
"""
from __future__ import annotations

import logging
from math import ceil, sqrt

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ...gpu_passes.core import read_color
from .. import registry
from ..core import FilterSpec, PixelSource, run_pass
from . import brushes, plan

log = logging.getLogger(__name__)

# The widest side of one gather. A chunk of this many squared stamps is
# two readbacks of 4 MB, and the default settings at 4096 need one chunk
# of a few thousand.
GATHER_SIDE = 512
# Stamps per draw. Each is a unit of the build, and the one-texel read
# after it keeps the driver's queue short, as `run_pass` does in bands.
DRAW_CHUNK = 32768
# Texels per side that one reduction pass takes the peak of.
PEAK_BLOCK = 8

LUMA = FilterSpec(
    name="painter_luma",
    apply_source="""
/* The picture the stroke direction is taken from: v2's luma of the
   straight colour, opaque so that the blur after it is a plain one. */
vec4 apply(ivec2 texel, vec4 c)
{
  float l = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
  return vec4(l, l, l, 1.0);
}
""",
)

SOBEL = FilterSpec(
    name="painter_sobel",
    apply_source="""
float ps_luma_at(ivec2 texel, int dx, int dy, ivec2 last)
{
  return texelFetch(source, clamp(texel + ivec2(dx, dy), ivec2(0), last), 0).r;
}

/* v2's Sobel, with the edge clamped as its padding did, and y up: rows
   run bottom-up here, so `gy` is the row above minus the row below. */
vec4 apply(ivec2 texel, vec4 c)
{
  ivec2 last = ivec2(target_size) - ivec2(1);
  float gx = ps_luma_at(texel, 1, -1, last) + 2.0 * ps_luma_at(texel, 1, 0, last)
           + ps_luma_at(texel, 1, 1, last) - ps_luma_at(texel, -1, -1, last)
           - 2.0 * ps_luma_at(texel, -1, 0, last) - ps_luma_at(texel, -1, 1, last);
  float gy = ps_luma_at(texel, -1, 1, last) + 2.0 * ps_luma_at(texel, 0, 1, last)
           + ps_luma_at(texel, 1, 1, last) - ps_luma_at(texel, -1, -1, last)
           - 2.0 * ps_luma_at(texel, 0, -1, last) - ps_luma_at(texel, 1, -1, last);
  return vec4(gx, gy, length(vec2(gx, gy)), 1.0);
}
""",
)

PEAK = FilterSpec(
    name="painter_peak",
    apply_source="""
/* The largest magnitude in one PEAK_BLOCK square of the level below,
   in every channel, so the next level reads it where this one did. */
vec4 apply(ivec2 texel, vec4 c)
{
  ivec2 last = textureSize(source, 0) - ivec2(1);
  float peak = 0.0;
  for (int y = 0; y < 8; y++) {
    for (int x = 0; x < 8; x++) {
      peak = max(peak, texelFetch(source, min(texel * 8 + ivec2(x, y), last), 0).b);
    }
  }
  return vec4(peak);
}
""",
)

GATHER = FilterSpec(
    name="painter_gather",
    apply_source="""
/* One stamp per texel: `second` holds where each one's centre is. */
vec4 apply(ivec2 texel, vec4 c)
{
  return texelFetch(source, ivec2(texelFetch(second, texel, 0).xy), 0);
}
""",
    reads_second=True,
)

PREMULTIPLY = FilterSpec(
    name="painter_premultiply",
    apply_source="""
vec4 apply(ivec2 texel, vec4 c)
{
  return vec4(c.rgb * c.a, c.a);
}
""",
)

FINISH = FilterSpec(
    name="painter_finish",
    apply_source="""
/* The canvas back to straight scene linear, which is what a kind hands
   the build. */
vec4 apply(ivec2 texel, vec4 c)
{
  vec3 rgb = c.a > 0.0 ? clamp(c.rgb / c.a, 0.0, 1.0) : vec3(0.0);
  return vec4(ps_to_linear(rgb), clamp(c.a, 0.0, 1.0));
}
""",
)

_STAMP_VERTEX = """
void main()
{
  v_coord = coord;
  v_color = color;
  gl_Position = vec4(position / target_size * 2.0 - 1.0, 0.0, 1.0);
}
"""

_STAMP_FRAGMENT = """
float ps_brush_at(ivec2 texel, ivec2 last)
{
  return texelFetch(atlas, clamp(texel, ivec2(0), last), 0).r;
}

/* Bilinear by hand: a texture made from Python samples nearest, and
   before Blender 5.1 cannot be told otherwise. The cell's gutter is
   what the outermost reads land on. */
void main()
{
  ivec2 last = textureSize(atlas, 0) - ivec2(1);
  vec2 at = v_coord - 0.5;
  ivec2 base = ivec2(floor(at));
  vec2 f = at - vec2(base);
  float low = mix(ps_brush_at(base, last), ps_brush_at(base + ivec2(1, 0), last), f.x);
  float high = mix(ps_brush_at(base + ivec2(0, 1), last),
                   ps_brush_at(base + ivec2(1, 1), last), f.x);
  out_color = v_color * mix(low, high, f.y);
}
"""

_stamp_shader = None


def build(node, texture, pool):
    """Paint *texture*, the stack below *node*, and return the result.

    A generator of ``(label, fraction)``, for `filters.layer_build` to
    drive. *texture* is scene linear and straight, and so is the texture
    returned; both belong to *pool*, and *texture* is given back to it
    once read.
    """
    settings = plan.Settings.of(node)
    width, height = texture.width, texture.height
    masks = brushes.masks(node.painter_brush)
    steps = plan.schedule(settings, width, height, masks)
    drawn = [plan.draws(settings, step, width, height, len(masks)) for step in steps]

    yield "reading the picture", 0.0
    encoded = _run(pool, registry.ENCODE_SRGB, texture)
    colors = _blurred(pool, encoded, settings.sigma, keep=True)
    yield "reading the picture", 0.03
    luma = _blurred(pool, _run(pool, LUMA, encoded, keep=True), settings.sigma, keep=False)
    canvas = _run(pool, PREMULTIPLY, encoded, keep=colors is encoded)
    yield "reading the picture", 0.06
    field = _run(pool, SOBEL, luma)
    peak = _peak(field) if settings.threshold > 0.0 else None

    x = np.concatenate([each.x for each in drawn])
    y = np.concatenate([each.y for each in drawn])
    chunk = min(GATHER_SIDE, width, height) ** 2
    sampled, gradients = [], []
    for start in range(0, len(x), chunk):
        yield "reading the picture", 0.1 + 0.1 * start / len(x)
        got = _gather((colors, field), x[start:start + chunk], y[start:start + chunk])
        sampled.append(got[0])
        gradients.append(got[1])
    sampled, gradients = np.concatenate(sampled), np.concatenate(gradients)
    pool.release(colors)
    pool.release(field)

    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(canvas,))
    limit = gpu.capabilities.max_texture_size_get()
    largest = max(mask.shape[0] for mask in masks)
    total, done, offset = len(x), 0, 0
    for step, numbers in zip(steps, drawn):
        rows = slice(offset, offset + step.count)
        offset += step.count
        stamps = plan.stamps(settings, step, numbers, sampled[rows], gradients[rows], peak)
        if len(stamps):
            columns, atlas_rows, cell = plan.atlas_layout(
                len(masks), min(step.size, largest), limit)
            image, origins = plan.atlas(masks, cell, columns, atlas_rows)
            atlas = _upload(image)
            for start in range(0, len(stamps), DRAW_CHUNK):
                yield (f"painting, step {step.index + 1} of {len(steps)}",
                       0.2 + 0.75 * (done + start) / total)
                geometry = plan.quads(stamps.part(start, start + DRAW_CHUNK),
                                      step.size, origins, cell)
                _draw_stamps(framebuffer, (width, height), atlas, geometry)
        done += step.count

    yield "finishing", 0.95
    return _run(pool, FINISH, canvas)


def _run(pool, spec: FilterSpec, texture, params: dict | None = None, *, keep: bool = False):
    """*spec* over *texture* into a target from *pool*.

    *texture* goes back to the pool afterwards unless *keep* says a later
    pass still reads it.
    """
    source = PixelSource.from_texture(texture)
    try:
        _framebuffer, result = run_pass(spec, source, pool.acquire(), params=params or {})
    finally:
        source.release()
    if not keep:
        pool.release(texture)
    return result


def _blurred(pool, texture, sigma: int, *, keep: bool):
    """*texture* through v2's gaussian: one pass per axis, cut off at two sigma.

    A sigma of zero returns *texture* itself, which is why the caller
    compares before giving either back.
    """
    if sigma <= 0:
        return texture
    radius = max(1, int(sigma * 2.0))
    current = texture
    for axis in ((1.0, 0.0), (0.0, 1.0)):
        params = {"direction": axis, "sigma": float(sigma), "radius": radius}
        current = _run(pool, registry.BLUR, current, params,
                       keep=keep and current is texture)
    return current


def _peak(field) -> float:
    """The largest gradient magnitude in *field*, reduced on the GPU."""
    current = field
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(field,))
    width, height = field.width, field.height
    while width > 1 or height > 1:
        width, height = ceil(width / PEAK_BLOCK), ceil(height / PEAK_BLOCK)
        source = PixelSource.from_texture(current)
        try:
            framebuffer, current = run_pass(
                PEAK, source, gpu.types.GPUTexture((width, height), format='RGBA32F'))
        finally:
            source.release()
    return float(read_color(framebuffer, 1, 1)[0, 0, 2])


def _gather(textures, x: np.ndarray, y: np.ndarray) -> list[np.ndarray]:
    """Each of *textures* at the texels ``(x, y)``, as ``(count, 4)`` arrays.

    One texel of a small target per stamp. The target is never larger
    than the textures read, which keeps the pass's own read of its
    source inside it.
    """
    count = len(x)
    columns = max(1, ceil(sqrt(count)))
    rows = ceil(count / columns)
    positions = np.zeros((rows * columns, 4), dtype=np.float32)
    positions[:count, 0] = x
    positions[:count, 1] = y
    where = gpu.types.GPUTexture(
        (columns, rows), format='RGBA32F',
        data=gpu.types.Buffer('FLOAT', positions.size, positions.ravel()))
    results = []
    for texture in textures:
        source = PixelSource.from_texture(texture)
        try:
            framebuffer, _target = run_pass(
                GATHER, source, gpu.types.GPUTexture((columns, rows), format='RGBA32F'),
                second=where)
            results.append(read_color(framebuffer, columns, rows).reshape(-1, 4)[:count])
        finally:
            source.release()
    return results


def _upload(image: np.ndarray):
    height, width = image.shape
    return gpu.types.GPUTexture(
        (width, height), format='R16F',
        data=gpu.types.Buffer('FLOAT', image.size, image.ravel()))


def _stamp_program():
    global _stamp_shader
    if _stamp_shader is None:
        interface = gpu.types.GPUStageInterfaceInfo("ps_painter_stamp_iface")
        interface.smooth('VEC2', "v_coord")
        interface.smooth('VEC4', "v_color")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC2', "target_size")
        info.sampler(0, 'FLOAT_2D', "atlas")
        info.vertex_in(0, 'VEC2', "position")
        info.vertex_in(1, 'VEC2', "coord")
        info.vertex_in(2, 'VEC4', "color")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_color")
        info.vertex_source(_STAMP_VERTEX)
        info.fragment_source(_STAMP_FRAGMENT)
        _stamp_shader = gpu.shader.create_from_info(info)
    return _stamp_shader


def _draw_stamps(framebuffer, size, atlas, geometry) -> None:
    """Draw one chunk of quads over the canvas, premultiplied "over", in order.

    Blending follows the order the triangles were submitted in, which is
    the order they were planned in, so a later stamp covers an earlier
    one exactly as in v2's loop.
    """
    positions, coords, colors, indices = geometry
    shader = _stamp_program()
    batch = batch_for_shader(shader, 'TRIS', {
        "position": positions, "coord": coords, "color": colors,
    }, indices=indices)
    sync = gpu.types.Buffer('FLOAT', 4)
    blend = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA_PREMULT')
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)
    gpu.state.face_culling_set('NONE')
    try:
        with framebuffer.bind():
            shader.uniform_float("target_size", (float(size[0]), float(size[1])))
            shader.uniform_sampler("atlas", atlas)
            batch.draw(shader)
            framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=sync)
    finally:
        gpu.state.blend_set(blend)


def release() -> None:
    """Give the stamp shader and the cached brushes back before the GPU context goes."""
    global _stamp_shader
    _stamp_shader = None
    brushes.release()
