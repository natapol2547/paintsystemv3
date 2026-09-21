# SPDX-License-Identifier: GPL-3.0-or-later
"""The painter's build: GPU passes around the numpy planning (PS-053).

`build` is the Painterly kind's build hook. `filters.layer_build` runs it
instead of a list of passes. The steps are:

1. Encode the stack below to sRGB. v2 painted stored byte values, and
   its look depends on that: the blur, the luma the gradient is taken
   on, and the edge of every "over" blend.
2. Blur the colour the stamps pick up, with v2's kernel. A Sobel pass
   over the blurred luma gives the gradient field. Its peak is found by
   a reduction, only when the edge threshold needs it.
3. Read both at every stamp centre `plan.draws` picked, in one small
   pass. This is the only readback before the result.
4. `plan.stamps` decides which stamps land and how. Each step's stamps
   are drawn as rotated quads into a premultiplied copy of the picture,
   from one atlas of that step's brushes.
5. Un-premultiply the canvas and decode it back to scene linear, because
   the layer build encodes whatever a kind returns.

The full-size textures come from the pool. The small ones (positions,
gathered values, the reduction) are made here and are freed when the
generator ends. The atlases are kept after it: each brush keeps the ones
its last build drew with, because a rebuild after a stroke below asks
for exactly those again.
"""
from __future__ import annotations

import logging
from math import ceil, sqrt

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ...gpu_passes.core import offscreen_state, read_color
from .. import registry
from ..core import FilterSpec, new_texture, run_pass
from . import brushes, plan

log = logging.getLogger(__name__)

# Largest side of one gather target. One chunk gathers up to
# `GATHER_SIDE` squared stamps, which is two readbacks (colour and
# gradient) of 4 MB each. The default settings at 4096 need only one
# chunk, of a few thousand stamps.
GATHER_SIDE = 512
# Stamps per draw call. Each draw is one unit of the build, and the
# one-texel read after it keeps the driver's queue short, like the bands
# of `run_pass`.
DRAW_CHUNK = 32768
# Side of the texel square that one reduction pass reduces to one texel.
PEAK_BLOCK = 8

LUMA = FilterSpec(
    name="painter_luma",
    apply_source="""
/* The image the stroke direction is taken from: v2's luma of the
   straight colour. Alpha is 1, so the blur after it is not weighted by
   alpha. */
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

/* v2's Sobel filter. Reads past the edge are clamped, matching v2's
   padding. Rows run bottom-up here, so y points up and `gy` is the row
   above minus the row below. */
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
    apply_source=f"const int PEAK_BLOCK = {PEAK_BLOCK};\n" + """
/* The largest magnitude in one PEAK_BLOCK square of the level below.
   It is written to every channel, so the next level can read it from
   `.b` just as this level did. */
vec4 apply(ivec2 texel, vec4 c)
{
  ivec2 last = textureSize(source, 0) - ivec2(1);
  float peak = 0.0;
  for (int y = 0; y < PEAK_BLOCK; y++) {
    for (int x = 0; x < PEAK_BLOCK; x++) {
      peak = max(peak, texelFetch(source, min(texel * PEAK_BLOCK + ivec2(x, y), last), 0).b);
    }
  }
  return vec4(peak);
}
""",
)

GATHER = FilterSpec(
    name="painter_gather",
    apply_source="""
/* One stamp per texel. `second` holds each stamp's centre, in texels. */
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
/* Turn the canvas back into straight scene linear, which is what a kind
   must return to the layer build. */
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

/* Bilinear filtering done by hand. A texture made from Python samples
   with nearest filtering, and before Blender 5.1 there is no way to
   change that. The outermost reads land on the cell's transparent
   gutter. */
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

# The atlases each brush's last build drew with. Maps a brush name
# (`settings.brush`) to ``{(cell, columns, rows): (texture, origins)}``,
# where origins are the lower-left texel of each brush in the atlas.
# Making and uploading them takes a tenth of a 4096 build. One build's
# worth per brush costs a few MB of video memory at the default settings.
# The name alone is a safe key only because a preset cannot change. A
# brush made from the user's own image would need its pixels in the key.
_atlases: dict[str, dict[tuple, tuple]] = {}


def build(settings, texture, pool):
    """Paint *texture* with *settings*, a `plan.Settings`, and return the result.

    This is a generator that yields ``(label, fraction)`` progress for
    `filters.layer_build`. *texture* and the returned texture are both
    straight scene linear, and both belong to *pool*. *texture* is given
    back to the pool once it has been read.
    """
    # The composite ran in the previous unit, so planning gets its own.
    yield "planning the strokes", 0.0
    width, height = texture.width, texture.height
    masks = brushes.masks(settings.brush)
    steps = plan.schedule(settings, width, height, brushes.areas(settings.brush))
    drawn = [plan.draws(settings, step, width, height, len(masks)) for step in steps]

    yield "reading the picture", 0.01
    encoded = _run(pool, registry.ENCODE_SRGB, texture)
    colors = yield from _blurred(pool, encoded, settings.sigma, keep=True,
                                 progress=("reading the picture", 0.02))
    yield "reading the picture", 0.03
    luma = yield from _blurred(pool, _run(pool, LUMA, encoded, keep=True), settings.sigma,
                               keep=False, progress=("reading the picture", 0.04))
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
    previous, used = _atlases.get(settings.brush, {}), {}
    total, done, offset = len(x), 0, 0
    for step, numbers in zip(steps, drawn):
        rows = slice(offset, offset + step.count)
        offset += step.count
        stamps = plan.stamps(settings, step, numbers, sampled[rows], gradients[rows], peak)
        if len(stamps):
            columns, atlas_rows, cell = plan.atlas_layout(
                len(masks), min(step.size, largest), limit)
            key = (cell, columns, atlas_rows)
            entry = used.get(key) or previous.get(key)
            if entry is None:
                image, origins = plan.atlas(masks, cell, columns, atlas_rows)
                entry = (_upload(image), origins)
            used[key] = entry
            atlas, origins = entry
            for start in range(0, len(stamps), DRAW_CHUNK):
                yield (f"painting, step {step.index + 1} of {len(steps)}",
                       0.2 + 0.75 * (done + start) / total)
                geometry = plan.quads(stamps.part(start, start + DRAW_CHUNK),
                                      step.size, origins, cell)
                _draw_stamps(framebuffer, (width, height), atlas, geometry)
        done += step.count
    # Replace rather than merge, so only the atlases of one build are kept.
    # A build abandoned before this line leaves the previous build's.
    _atlases[settings.brush] = used

    yield "finishing", 0.95
    return _run(pool, FINISH, canvas)


def _run(pool, spec: FilterSpec, texture, params: dict | None = None, *, keep: bool = False):
    """Run *spec* over *texture* into a target from *pool*, and return it.

    *texture* goes back to the pool afterwards, unless *keep* is set
    because a later pass still reads it.
    """
    _framebuffer, result = run_pass(spec, texture, pool.acquire(), params=params or {})
    if not keep:
        pool.release(texture)
    return result


def _blurred(pool, texture, sigma: float, *, keep: bool, progress: tuple[str, float]):
    """Blur *texture* with v2's gaussian, one pass per axis, cut off at two sigma.

    This is a generator. It yields *progress* between the two passes, so
    each pass is a unit of its own (about 50 ms at 4096). A sigma of zero
    returns *texture* itself, so the caller checks for that before it
    gives either texture back to the pool.
    """
    if sigma <= 0:
        return texture
    radius = max(1, int(sigma * 2.0))
    current = texture
    for index, axis in enumerate(((1.0, 0.0), (0.0, 1.0))):
        if index:
            yield progress
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
        framebuffer, current = run_pass(PEAK, current, new_texture((width, height), 'RGBA32F'))
    return float(read_color(framebuffer, 1, 1)[0, 0, 2])


def _gather(textures, x: np.ndarray, y: np.ndarray) -> list[np.ndarray]:
    """Each of *textures* read at the texels ``(x, y)``, as ``(count, 4)`` arrays.

    Each stamp gets one texel of a small target. The target is never
    larger than the textures it reads, because the pass also reads its
    source at the target's own texel, and that read must stay inside the
    source.
    """
    count = len(x)
    columns = max(1, ceil(sqrt(count)))
    rows = ceil(count / columns)
    positions = np.zeros((rows * columns, 4), dtype=np.float32)
    positions[:count, 0] = x
    positions[:count, 1] = y
    where = new_texture((columns, rows), 'RGBA32F',
                        data=gpu.types.Buffer('FLOAT', positions.size, positions.ravel()))
    results = []
    for texture in textures:
        framebuffer, _target = run_pass(
            GATHER, texture, new_texture((columns, rows), 'RGBA32F'), second=where)
        results.append(read_color(framebuffer, columns, rows).reshape(-1, 4)[:count])
    return results


def _upload(image: np.ndarray):
    height, width = image.shape
    return new_texture((width, height), 'R16F',
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
    """Draw one chunk of stamp quads over the canvas with premultiplied "over".

    The GPU blends triangles in the order they are submitted, which is
    the order they were planned in. So a later stamp covers an earlier
    one, exactly as in v2's loop.
    """
    positions, coords, colors, indices = geometry
    shader = _stamp_program()
    batch = batch_for_shader(shader, 'TRIS', {
        "position": positions, "coord": coords, "color": colors,
    }, indices=indices)
    sync = gpu.types.Buffer('FLOAT', 4)
    with offscreen_state('ALPHA_PREMULT'), framebuffer.bind():
        shader.uniform_float("target_size", (float(size[0]), float(size[1])))
        shader.uniform_sampler("atlas", atlas)
        batch.draw(shader)
        framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=sync)


def release() -> None:
    """Free the stamp shader, the atlases and the cached brushes.

    Called before the GPU context goes away.
    """
    global _stamp_shader
    _stamp_shader = None
    _atlases.clear()
    brushes.release()
