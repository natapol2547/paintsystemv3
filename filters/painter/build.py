# SPDX-License-Identifier: GPL-3.0-or-later
"""The painter's build: GPU passes around the numpy planning (PS-053).

`build` is the Painterly kind's build hook. `filters.layer_build` runs it
instead of a list of passes, with what `read_inputs` read when the build
started. The steps are:

1. Find the UV islands of the mesh and the ways across its seams
   (`seams`), or take them from the cache.
2. Encode the stack below to sRGB. v2 painted stored byte values, and
   its look depends on that: the blur, the luma the gradient is taken
   on, and the edge of every "over" blend.
3. Blur the colour the stamps pick up, with v2's kernel. A Sobel pass
   over the blurred luma gives the gradient field. Its peak is found by
   a reduction, only when the edge threshold needs it.
4. Draw the island of every texel (`texel_map.draw_islands`).
5. Read the colour, the gradient and the island at every stamp centre
   `plan.draws` picked, in one small pass each. This is the only
   readback before the result.
6. `plan.stamps` decides which stamps land and how. Each step's stamps
   are drawn as rotated quads (`drawing.quads`) into a premultiplied
   copy of the picture, from one atlas of that step's brushes. A stamp
   paints only its own island and texels of no island. The part of it
   that runs over a seam is carried across as pieces
   (`seams.candidates` and `seams.pieces`), which paint the island on
   the other side and its margin. On the island's own texels they paint
   only where what they carry is off the stamp's island. A depth slot
   per stamp keeps its quad and pieces from painting one texel twice.
7. Un-premultiply the canvas and decode it back to scene linear, because
   the layer build encodes whatever a kind returns.

The full-size textures come from the pool, except the island map and
the depth texture, which have formats of their own. They and the small
ones (positions, gathered values, the reduction) are made here and are
freed when the generator ends. The atlases are kept after it: each brush
keeps the ones its last build drew with, because a rebuild after a stroke
below asks for exactly those again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil, sqrt

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ...gpu_passes import texel_map
from ...gpu_passes.core import offscreen_state, read_color
from .. import registry
from ..core import FilterSpec, new_texture, run_pass
from . import brushes, drawing, plan, seams

log = logging.getLogger(__name__)

# Largest side of one gather target. One chunk gathers up to
# `GATHER_SIDE` squared stamps, which is three readbacks (colour,
# gradient and island) of 4 MB each. The default settings at 4096 need
# only one chunk, of a few thousand stamps.
GATHER_SIDE = 512
# Stamps per draw call. Each draw is one unit of the build, and the
# one-texel read after it keeps the driver's queue short, like the bands
# of `run_pass`.
DRAW_CHUNK = 32768
# Most crossings one draw carries stamps over. Cutting them costs about
# 2.5 microseconds each, so a draw's planning stays near 20 ms. A stamp
# with more crossings than this gets a draw of its own.
PLAN_CHUNK = 8192
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
  v_island = island;
  v_source = source;
  /* z is the stamp's depth slot (`drawing.geometry`). */
  gl_Position = vec4(position.xy / target_size * 2.0 - 1.0, position.z, 1.0);
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
  /* `v_island` is the island a part paints, then the island a piece
     carried across a seam leaves, or 0 for a stamp's quad. A quad paints
     its island and texels of no island. The island next to it in the
     image sits somewhere else on the mesh, so a stroke running into it
     would paint where it does not belong. A piece paints only the island
     on the other side. On that island's own texels it paints only where
     the part of the stamp it carries is off the stamp's own island,
     because the quad paints that part where it is. Its margin takes the
     part from just inside the stamp's island, which filtering reads past
     the far edge. A margin texel reads minus its island. */
  float here = texelFetch(islands, ivec2(gl_FragCoord.xy), 0).r;
  bool kept = abs(here) == v_island.x || (v_island.y == 0.0 && here == 0.0);
  if (kept && v_island.y > 0.0 && here > 0.0) {
    ivec2 source = ivec2(floor(v_source));
    if (all(greaterThanEqual(source, ivec2(0))) && all(lessThan(source, textureSize(islands, 0)))) {
      kept = texelFetch(islands, source, 0).r != v_island.y;
    }
  }
  if (!kept) {
    discard;
  }
  ivec2 last = textureSize(atlas, 0) - ivec2(1);
  vec2 at = v_coord - 0.5;
  ivec2 base = ivec2(floor(at));
  vec2 f = at - vec2(base);
  float low = mix(ps_brush_at(base, last), ps_brush_at(base + ivec2(1, 0), last), f.x);
  float high = mix(ps_brush_at(base + ivec2(0, 1), last),
                   ps_brush_at(base + ivec2(1, 1), last), f.x);
  out_color = v_color * mix(low, high, f.y);
  /* A texel the brush leaves empty must not take the stamp's depth slot,
     or another part of the stamp could not paint it. */
  if (out_color.a == 0.0) {
    discard;
  }
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


@dataclass(frozen=True)
class Inputs:
    """What a build paints with, read when it starts."""

    settings: plan.Settings
    snapshot: seams.Snapshot


def read_inputs(node, resolved) -> Inputs:
    """The layer's settings and a copy of its mesh, for `build`.

    *resolved* is the layer's `filters.layer_plan.InputPlan`.
    `filters.layer_build.steps` calls this in the same tick as the
    resolve that checked the mesh, and nothing after it reads the mesh.
    So the build cannot fail on a mesh that enters Edit Mode or goes away
    while it runs.
    """
    obj = resolved.surface
    snapshot = seams.snapshot(obj, texel_map.resolve_uv_map(obj, resolved.uv_map), node.id_data,
                              bpy.context.evaluated_depsgraph_get())
    return Inputs(settings=plan.Settings.of(node), snapshot=snapshot)


def build(inputs, texture, pool):
    """Paint *texture* with *inputs*, from `read_inputs`, and return the result.

    This is a generator that yields ``(label, fraction)`` progress for
    `filters.layer_build`. *texture* and the returned texture are both
    straight scene linear, and both belong to *pool*. *texture* is given
    back to the pool once it has been read.
    """
    settings, snapshot = inputs.settings, inputs.snapshot
    # The composite ran in the previous unit, so planning gets its own.
    yield "planning the strokes", 0.0
    width, height = texture.width, texture.height
    masks = brushes.masks(settings.brush)
    steps = plan.schedule(settings, width, height, brushes.areas(settings.brush))
    drawn = [plan.draws(settings, step, width, height, len(masks)) for step in steps]
    index = yield from seams.index_of(snapshot, ("reading the UV seams", 0.005))

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

    # Drawn after the Sobel pass has given the luma back, so the most
    # video memory the analysis holds at once does not grow.
    yield "reading the UV seams", 0.08
    islands = new_texture((width, height), 'R32F')
    yield from texel_map.draw_islands(islands, snapshot.uv, snapshot.tri_corners,
                                      seams.triangle_islands(snapshot, index),
                                      ("reading the UV seams", 0.09))

    x = np.concatenate([each.x for each in drawn])
    y = np.concatenate([each.y for each in drawn])
    chunk = min(GATHER_SIDE, width, height) ** 2
    sampled, gradients, owners = [], [], []
    for start in range(0, len(x), chunk):
        yield "reading the picture", 0.1 + 0.1 * start / len(x)
        got = _gather((colors, field, islands), x[start:start + chunk], y[start:start + chunk])
        sampled.append(got[0])
        gradients.append(got[1])
        # A stamp centred in an island's margin is that island's.
        owners.append(np.abs(got[2][:, 0]).astype(np.int32))
    sampled, gradients, owners = np.concatenate(sampled), np.concatenate(gradients), np.concatenate(owners)
    pool.release(colors)
    pool.release(field)

    # Neither a framebuffer nor a bound sampler keeps a texture alive, so
    # this frame holds the depth texture and the island map while drawing.
    depth = new_texture((width, height), 'DEPTH_COMPONENT32F')
    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(canvas,), depth_slot=depth)
    crossings = seams.crossings(snapshot, index, width, height)
    limit = gpu.capabilities.max_texture_size_get()
    largest = max(mask.shape[0] for mask in masks)
    previous, used = _atlases.get(settings.brush, {}), {}
    total, done, offset = len(x), 0, 0
    for step, numbers in zip(steps, drawn):
        rows = slice(offset, offset + step.count)
        offset += step.count
        stamps = plan.stamps(settings, step, numbers, sampled[rows], gradients[rows], peak,
                             owners[rows])
        if len(stamps):
            columns, atlas_rows, cell = drawing.atlas_layout(
                len(masks), min(step.size, largest), limit)
            key = (cell, columns, atlas_rows)
            entry = used.get(key) or previous.get(key)
            if entry is None:
                image, origins = drawing.atlas(masks, cell, columns, atlas_rows)
                entry = (_upload(image), origins)
            used[key] = entry
            atlas, origins = entry
            label = f"painting, step {step.index + 1} of {len(steps)}"
            for start in range(0, len(stamps), DRAW_CHUNK):
                yield label, 0.2 + 0.75 * (done + start) / total
                part = stamps.part(start, start + DRAW_CHUNK)
                corners, coords = drawing.quads(part, step.size, origins, cell)
                stamp, crossing = seams.candidates(crossings, corners.mean(axis=1), part.owner,
                                                   step.size)
                for number, (first, last, low, high) in enumerate(_blocks(len(part), stamp)):
                    if number:
                        yield label, 0.2 + 0.75 * (done + start + first) / total
                    carried = seams.pieces(crossings, corners[first:last], coords[first:last],
                                           stamp[low:high] - first, crossing[low:high])
                    geometry = drawing.geometry(corners[first:last], coords[first:last],
                                                part.color[first:last], part.owner[first:last],
                                                carried)
                    _draw_stamps(framebuffer, (width, height), atlas, islands, geometry)
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


def _blocks(count: int, stamp: np.ndarray):
    """Cut *count* stamps into draws of at most `PLAN_CHUNK` crossings each.

    *stamp* is the stamp of every crossing from `seams.candidates`, in
    order. Yields ``(first, last, low, high)``: the stamps from *first* up
    to *last*, and their crossings from *low* up to *high*. A draw always
    holds whole stamps, because a stamp's depth slot only keeps its parts
    apart within one draw.
    """
    ends = np.searchsorted(stamp, np.arange(1, count + 1))
    first = low = 0
    while first < count:
        last = max(first + 1, int(np.searchsorted(ends, low + PLAN_CHUNK, side='right')))
        high = int(ends[last - 1])
        yield first, last, low, high
        first, low = last, high


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
        interface.smooth('VEC2', "v_source")
        interface.flat('VEC2', "v_island")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('VEC2', "target_size")
        info.sampler(0, 'FLOAT_2D', "atlas")
        info.sampler(1, 'FLOAT_2D', "islands")
        info.vertex_in(0, 'VEC3', "position")
        info.vertex_in(1, 'VEC2', "coord")
        info.vertex_in(2, 'VEC4', "color")
        info.vertex_in(3, 'VEC2', "island")
        info.vertex_in(4, 'VEC2', "source")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_color")
        info.vertex_source(_STAMP_VERTEX)
        info.fragment_source(_STAMP_FRAGMENT)
        _stamp_shader = gpu.shader.create_from_info(info)
    return _stamp_shader


def _draw_stamps(framebuffer, size, atlas, islands, geometry) -> None:
    """Draw one chunk of stamps from `drawing.geometry` over the canvas with premultiplied "over".

    *islands* is the island map the stamps keep to, the size of the
    canvas, and *framebuffer* has a depth texture for the stamps' depth
    slots. The GPU blends triangles in the order they are submitted,
    which is the order they were planned in. The depth is cleared first,
    because the slots start again with every draw.
    """
    positions, coords, colors, part_islands, sources, indices = geometry
    shader = _stamp_program()
    batch = batch_for_shader(shader, 'TRIS', {
        "position": positions, "coord": coords, "color": colors, "island": part_islands, "source": sources,
    }, indices=indices)
    sync = gpu.types.Buffer('FLOAT', 4)
    with offscreen_state('ALPHA_PREMULT'), framebuffer.bind():
        # `offscreen_state` turns the depth test off, so it is set here.
        gpu.state.depth_test_set('LESS')
        gpu.state.depth_mask_set(True)
        framebuffer.clear(depth=1.0)
        shader.uniform_float("target_size", (float(size[0]), float(size[1])))
        shader.uniform_sampler("atlas", atlas)
        shader.uniform_sampler("islands", islands)
        batch.draw(shader)
        framebuffer.read_color(0, 0, 1, 1, 4, 0, 'FLOAT', data=sync)


def release() -> None:
    """Free the stamp shader, the atlases, the cached brushes and the kept islands.

    Called before the GPU context goes away.
    """
    global _stamp_shader
    _stamp_shader = None
    _atlases.clear()
    brushes.release()
    seams.release()
