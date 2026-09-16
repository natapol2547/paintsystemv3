"""Selection masks, built on the GPU from the ops on the tree (PS-091).

A mask is one `R32F` texture the size of the layer image (or of one UDIM
tile of it), holding coverage from 0 to 1 per texel. It is derived data:
`get_mask` builds it from `PaintSystemSelection.ops` and nothing stores
it, so undo, redo and a reload need no mask history.

Every op is one full-screen pass. The fragment shader evaluates the op's
coverage at the texel centre and combines it with the previous mask,
which it reads with `texelFetch`, so the passes ping-pong between two
targets and never blend:

- `REPLACE` writes the coverage `c`, `ADD` writes `max(m, c)`,
  `SUBTRACT` writes `min(m, 1 - c)` and `INTERSECT` writes `min(m, c)`.
  `INVERT` writes `1 - m` whatever its mode says. A first op with any
  mode other than `REPLACE` combines with an empty mask.
- Coverage comes from a signed distance `s` in texels, positive inside.
  With `half_width = 0.5 * max(feather, 1 if antialias else 0)` it is
  `s > 0` for a hard edge and the smoothstep of
  `(s + half_width) / (2 * half_width)` otherwise, so a feathered edge
  rises from 0 to 1 over the feather width, centred on the outline.
  With a hard edge (no feather, anti-alias off), a box or ellipse
  excludes texel centres that lie exactly on its outline. A lasso uses
  the half-open even-odd rule, which counts a centre on a right or
  bottom edge and not one on a left or top edge. The same rectangle
  drawn as a box and as a lasso can therefore differ by one column and
  one row when its edges pass through texel centres.
- Box and ellipse distances are analytic. The ellipse uses Eberly's
  robust distance with the root bisected in `u = s + 1`, which float32
  resolves to within about 3e-7 of the longer radius; texels far from
  the outline skip it. Shape parameters are split into whole and
  fractional texels before upload: a float32 texel coordinate alone is
  off by up to 2.4e-4 texels at 4K, which moves feathered coverage by
  more than 1e-5.
- Lasso fill and distance come from the tables `outline.py` builds.

Masks are cached by content: the key is the digest of the op chain, the
target size and the UDIM tile (`PaintSystemSelection.prefix_digests`),
so an undo, a redo or a second tree with the same ops finds the mask
already built. A build caches the final mask and the one before it,
which makes removing the last op, or an undo of an appended op, free.
Before each build, the least recently used masks are evicted until the
build's targets fit `CACHE_BUDGET`. The cached prefix the build resumes
from is never evicted, so after a build the cache can hold that one
mask over the budget. Any mask, including the one the previous call
returned, may be evicted by the next `get_mask`. An evicted mask's
`texture` raises `ReferenceError`, so callers must not keep masks
across calls, redraws or timer ticks but call `get_mask` or `peek_mask`
again.

A build draws in bands of `BAND_ROWS` rows and reads one texel back
between bands. That bounds the GPU time of any single command, so a
slow software rasteriser or a dense lasso with a wide feather is far
less likely to trip a driver watchdog (i915 preempts after 640 ms,
Windows after 2 s).

The passes set blend, depth test and depth write and put them back.
They also turn face culling off and colour writes on, and leave them
that way, because `gpu.state` cannot read either.
"""
import logging

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes import core
from ..props.selection import FEATHER_MAX, OUTLINELESS_KINDS, POINTS_KEY, SPACELESS_KINDS, points_view
from . import outline

log = logging.getLogger(__name__)

CACHE_BUDGET = 512 << 20
"""Video memory the cached masks may hold, in bytes. A 4K mask is 64 MB."""

POOL_LIMIT = 2
"""Evicted textures kept for reuse by the next build of the same size."""

BAND_ROWS = 512
"""Rows drawn per band before the GPU is made to finish."""

MAX_TEXELS = 1 << 26
"""Largest mask, in texels: 8192 x 8192, or 256 MB per mask."""

MIN_RADIUS = 1e-3
"""Ellipse radius in texels below which the ellipse covers nothing."""

SELF_TEST_SIZE = 64
"""Width and height of the self-test target, in texels."""

SELF_TEST_WIDE_SIZE = (140, 64)
"""Width and height of the second self-test target, in texels: three
span columns wide, so its lasso tables use more than one."""

SELF_TEST_TOLERANCE = 1e-4
"""Largest difference from the expected values the self-test accepts."""

SUPPORTED_KINDS = frozenset(('BOX', 'ELLIPSE', 'LASSO', 'ALL', 'INVERT'))
"""Op kinds this module rasterises. `FACES`, `RASTER` and `TRANSFORM`
arrive with the tools that create them (PS-093, PS-094)."""

_MODE_UNIFORMS = {'REPLACE': 0, 'ADD': 1, 'SUBTRACT': 2, 'INTERSECT': 3}
_SHAPE_ALL, _SHAPE_BOX, _SHAPE_ELLIPSE, _SHAPE_INVERT, _SHAPE_NOTHING = range(5)

_UNSUPPORTED_KIND_MESSAGES = {
    'FACES': "Selections with a faces operation cannot be built yet",
    'RASTER': "Selections with a raster operation cannot be built yet",
    'TRANSFORM': "Selections with a transform operation cannot be built yet",
}
_MESSAGES = {
    'NO_GPU': "This Blender session has no GPU context, so the selection cannot be built",
    'NO_SIZE': "The selection has no image with pixels to take its size from",
    'TOO_LARGE': "The image is too large for a selection mask",
    'VIEW': "Selections drawn in the 3D view cannot be built yet",
    'POINTS': "A selection operation has malformed points",
    'TOO_COMPLEX': "The lasso outline is too complex to build",
    'SELF_TEST': "The GPU failed the selection self-test, so selections are disabled in this session",
    'GPU_ERROR': "The GPU could not build the selection right now; try again",
}

_VERTEX_SOURCE = """
void main()
{
  /* One quad per band: x spans the target, y spans rows.x to rows.y. */
  v_texel = vec2(position.x * target_size.x, mix(rows.x, rows.y, position.y));
  gl_Position = vec4(v_texel / target_size * 2.0 - 1.0, 0.0, 1.0);
}
"""

_COMMON_SOURCE = """
float edge_profile(float s)
{
  if (half_width <= 0.0) {
    return s > 0.0 ? 1.0 : 0.0;
  }
  float t = clamp((s + half_width) / (2.0 * half_width), 0.0, 1.0);
  return t * t * (3.0 - 2.0 * t);
}

float combine(float previous_value, float coverage)
{
  if (mode == 0) {
    return coverage;
  }
  if (mode == 1) {
    return max(previous_value, coverage);
  }
  if (mode == 2) {
    return min(previous_value, 1.0 - coverage);
  }
  return min(previous_value, coverage);
}

float previous_at(ivec2 texel)
{
  return use_previous != 0 ? texelFetch(previous, texel, 0).r : 0.0;
}
"""

_SHAPE_FRAGMENT_SOURCE = _COMMON_SOURCE + """
/* Eberly, "Distance from a Point to an Ellipse, an Ellipsoid, or a
   Hyperellipsoid". The root is bracketed in u = s + 1 rather than s, so
   the bracket never needs a value near -1 that float32 cannot hold. */
float ellipse_root(float r0_minus_1, float n0, float z1, float g)
{
  float u0 = z1;
  float u1 = g < 0.0 ? 1.0 : length(vec2(n0, z1));
  float u = u0;
  for (int i = 0; i < 160; i++) {
    u = 0.5 * (u0 + u1);
    if (u == u0 || u == u1) {
      break;
    }
    float ratio0 = n0 / (u + r0_minus_1);
    float ratio1 = z1 / u;
    float value = ratio0 * ratio0 + ratio1 * ratio1 - 1.0;
    if (value > 0.0) {
      u0 = u;
    }
    else if (value < 0.0) {
      u1 = u;
    }
    else {
      break;
    }
  }
  return u;
}

/* Unsigned distance from (y0, y1) >= 0 to the ellipse with radii
   e0 >= e1 > 0. */
float ellipse_distance(float e0, float e1, float y0, float y1)
{
  float r0_minus_1 = (e0 - e1) * (e0 + e1) / (e1 * e1);
  if (y1 > 0.0) {
    if (y0 > 0.0) {
      float z0 = y0 / e0;
      float z1 = y1 / e1;
      float g = z0 * z0 + z1 * z1 - 1.0;
      if (g == 0.0) {
        return 0.0;
      }
      float u = ellipse_root(r0_minus_1, (r0_minus_1 + 1.0) * z0, z1, g);
      return abs(1.0 - u) * length(vec2(y0 / (u + r0_minus_1), y1 / u));
    }
    return abs(y1 - e1);
  }
  float numer0 = e0 * y0;
  float denom0 = (e0 - e1) * (e0 + e1);
  if (numer0 < denom0) {
    float xde0 = numer0 / denom0;
    return length(vec2(e0 * xde0 - y0, e1 * sqrt(1.0 - xde0 * xde0)));
  }
  return abs(y0 - e0);
}

void main()
{
  ivec2 texel = ivec2(floor(v_texel));
  if (shape == 3) {
    out_mask = 1.0 - previous_at(texel);
    return;
  }
  float previous_value = mode == 0 ? 0.0 : previous_at(texel);
  float coverage = 1.0;
  if (shape == 4) {
    coverage = 0.0;
  }
  else if (shape == 1) {
    /* Box from shape_whole.xy + shape_fraction.xy to
       shape_whole.zw + shape_fraction.zw. */
    vec2 below = (shape_whole.xy - vec2(texel)) + (shape_fraction.xy - 0.5);
    vec2 above = (vec2(texel) - shape_whole.zw) + (0.5 - shape_fraction.zw);
    vec2 q = max(below, above);
    float outside = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0);
    coverage = edge_profile(-outside);
  }
  else if (shape == 2) {
    /* Ellipse with its centre in xy and its radii in zw. */
    vec2 y = abs((shape_whole.xy - vec2(texel)) + (shape_fraction.xy - 0.5));
    vec2 e = shape_whole.zw + shape_fraction.zw;
    if (e.x < e.y) {
      y = y.yx;
      e = e.yx;
    }
    float z0 = y.x / e.x;
    float z1 = y.y / e.y;
    /* The ellipse scaled about its centre through the texel stays at
       least |rho - 1| * e1 from the outline, so a texel further than the
       half width needs no distance at all. */
    float bound = abs(length(vec2(z0, z1)) - 1.0) * e.y;
    if (bound >= half_width) {
      coverage = z0 * z0 + z1 * z1 < 1.0 ? 1.0 : 0.0;
    }
    else {
      float d = ellipse_distance(e.x, e.y, y.x, y.y);
      coverage = edge_profile(z0 * z0 + z1 * z1 < 1.0 ? d : -d);
    }
  }
  out_mask = combine(previous_value, coverage);
}
"""

_LASSO_FRAGMENT_SOURCE = _COMMON_SOURCE + """
ivec2 data_texel(int index)
{
  return ivec2(index % DATA_WIDTH, index / DATA_WIDTH);
}

float segment_distance(vec2 p, vec2 a, vec2 b)
{
  vec2 ab = b - a;
  vec2 ap = p - a;
  float length2 = dot(ab, ab);
  float t = length2 > 0.0 ? clamp(dot(ap, ab) / length2, 0.0, 1.0) : 0.0;
  return length(ap - ab * t);
}

void main()
{
  ivec2 texel = ivec2(floor(v_texel));
  float previous_value = mode == 0 ? 0.0 : previous_at(texel);

  vec4 span = texelFetch(row_spans, ivec2(texel.x / span_width, texel.y), 0);
  int start = int(span.r);
  int packed_count = int(span.g);
  int count = packed_count / 2;
  int crossings = packed_count % 2;
  float x = float(texel.x);
  for (int i = 0; i < count; i++) {
    if (texelFetch(keys, data_texel(start + i), 0).r > x) {
      break;
    }
    crossings++;
  }
  bool inside = (crossings % 2) == 1;

  float coverage;
  if (half_width <= 0.0) {
    coverage = inside ? 1.0 : 0.0;
  }
  else {
    ivec2 cell = texel / cell_size;
    vec4 record = texelFetch(cells, cell, 0);
    int offset = int(record.r);
    int list_length = int(record.g);
    vec2 local = vec2(texel - cell * cell_size) + 0.5;
    float from_centre = length(local - 0.5 * float(cell_size));
    float d = half_width;
    for (int i = 0; i < list_length; i++) {
      ivec2 at = data_texel(offset + i);
      /* Entries are sorted by distance from the cell centre, so no later
         entry can be closer than d. */
      if (texelFetch(bounds, at, 0).r - from_centre >= d) {
        break;
      }
      vec4 segment = texelFetch(entries, at, 0);
      d = min(d, segment_distance(local, segment.xy, segment.zw));
    }
    coverage = edge_profile(inside ? d : -d);
  }
  out_mask = combine(previous_value, coverage);
}
""".replace("DATA_WIDTH", str(outline.DATA_WIDTH))

_QUANTISE_VERTEX_SOURCE = """
void main()
{
  gl_Position = vec4(position * 2.0 - 1.0, 0.0, 1.0);
}
"""

_QUANTISE_FRAGMENT_SOURCE = """
void main()
{
  out_value = floor(texelFetch(mask, ivec2(gl_FragCoord.xy), 0).r * 255.0 + 0.5) / 255.0;
}
"""

# Two counter-clockwise triangles over the unit square.
_QUAD = {"position": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0), (1.0, 1.0), (0.0, 1.0))}

_gpu: dict = {}


def _mask_shader(name: str, fragment: str, lasso: bool) -> gpu.types.GPUShader:
    """A mask pass shader. Push constants stay under Vulkan's 128 bytes."""
    interface = gpu.types.GPUStageInterfaceInfo(f"ps_selection_{name}_interface")
    interface.smooth('VEC2', "v_texel")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('VEC2', "target_size")
    info.push_constant('VEC2', "rows")
    info.push_constant('INT', "mode")
    info.push_constant('INT', "use_previous")
    info.push_constant('FLOAT', "half_width")
    if lasso:
        info.push_constant('INT', "span_width")
        info.push_constant('INT', "cell_size")
    else:
        info.push_constant('INT', "shape")
        info.push_constant('VEC4', "shape_whole")
        info.push_constant('VEC4', "shape_fraction")
    info.sampler(0, 'FLOAT_2D', "previous")
    if lasso:
        info.sampler(1, 'FLOAT_2D', "row_spans")
        info.sampler(2, 'FLOAT_2D', "keys")
        info.sampler(3, 'FLOAT_2D', "cells")
        info.sampler(4, 'FLOAT_2D', "bounds")
        info.sampler(5, 'FLOAT_2D', "entries")
    info.vertex_in(0, 'VEC2', "position")
    info.vertex_out(interface)
    info.fragment_out(0, 'FLOAT', "out_mask")
    info.vertex_source(_VERTEX_SOURCE)
    info.fragment_source(fragment)
    return gpu.shader.create_from_info(info)


def _quantise_shader() -> gpu.types.GPUShader:
    """Rounds a mask to 8 bits on the GPU, for `SelectionMask.read_bytes`."""
    info = gpu.types.GPUShaderCreateInfo()
    info.sampler(0, 'FLOAT_2D', "mask")
    info.vertex_in(0, 'VEC2', "position")
    info.fragment_out(0, 'FLOAT', "out_value")
    info.vertex_source(_QUANTISE_VERTEX_SOURCE)
    info.fragment_source(_QUANTISE_FRAGMENT_SOURCE)
    return gpu.shader.create_from_info(info)


def _resources() -> dict:
    """Shaders, batches and the placeholder texture, built once per session."""
    if not _gpu:
        shape = _mask_shader("shape", _SHAPE_FRAGMENT_SOURCE, False)
        lasso = _mask_shader("lasso", _LASSO_FRAGMENT_SOURCE, True)
        quantise = _quantise_shader()
        _gpu.update(
            shape=(shape, batch_for_shader(shape, 'TRIS', _QUAD)),
            lasso=(lasso, batch_for_shader(lasso, 'TRIS', _QUAD)),
            quantise=(quantise, batch_for_shader(quantise, 'TRIS', _QUAD)),
            # Bound to every sampler a pass does not read: an unbound
            # sampler is an error on Vulkan.
            placeholder=gpu.types.GPUTexture((1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [0.0])),
        )
    return _gpu


class MaskUnavailable(RuntimeError):
    """A mask cannot be built. `str()` is a message fit for the UI.

    `reason` is one of `NO_GPU`, `NO_SIZE`, `TOO_LARGE`, `UNSUPPORTED`,
    `TOO_COMPLEX`, `SELF_TEST` and `GPU_ERROR`. `UNSUPPORTED` covers an
    op kind or space this module cannot build yet and an op with
    malformed points. `GPU_ERROR` means the GPU raised while building, as
    it does with no active context, so a later call may succeed.
    `op_index` is the index in `selection.ops` of the op at fault, or -1
    when no single op is.
    """

    def __init__(self, reason: str, message: str, op_index: int = -1):
        super().__init__(message)
        self.reason = reason
        self.op_index = op_index


class SelectionMask:
    """One built mask: an `R32F` texture, row 0 at the bottom like `Image.pixels`.

    The cache owns the texture. Once the mask is evicted, `alive` is False
    and `texture`, `read` and `read_bytes` raise `ReferenceError`.
    """

    __slots__ = ('_texture', 'width', 'height', 'tile', 'key', 'alive')

    def __init__(self, texture, width: int, height: int, tile: int, key: bytes):
        self._texture = texture
        self.width = width
        self.height = height
        self.tile = tile
        self.key = key
        self.alive = True

    @property
    def texture(self) -> gpu.types.GPUTexture:
        if not self.alive:
            raise ReferenceError("This selection mask was evicted; call get_mask() again")
        return self._texture

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def video_memory(self) -> int:
        return self.width * self.height * 4

    def read(self) -> np.ndarray:
        """The mask as float32 `(height, width)`, exactly as the passes wrote it."""
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(self.texture,))
        buffer = gpu.types.Buffer('FLOAT', self.width * self.height)
        with framebuffer.bind():
            framebuffer.read_color(0, 0, self.width, self.height, 1, 0, 'FLOAT', data=buffer)
        return np.frombuffer(buffer, dtype=np.float32).reshape(self.height, self.width).copy()

    def read_bytes(self) -> np.ndarray:
        """The mask as uint8 `(height, width)`, each value `floor(v * 255 + 0.5)`.

        Quantised on the GPU into an `R8` target, so the read back is a
        quarter of `read`'s. Reading an `R32F` texture as bytes directly
        returns zeros on Vulkan. The rounding runs in float32, so a value
        within float32 error of a half step can round the other way than
        the same formula in float64 would.
        """
        shader, batch = _resources()["quantise"]
        target = gpu.types.GPUTexture((self.width, self.height), format='R8')
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
        buffer = gpu.types.Buffer('UBYTE', self.width * self.height)
        with _State():
            with framebuffer.bind():
                shader.uniform_sampler("mask", self.texture)
                batch.draw(shader)
                framebuffer.read_color(0, 0, self.width, self.height, 1, 0, 'UBYTE', data=buffer)
        return np.frombuffer(buffer, dtype=np.uint8).reshape(self.height, self.width).copy()


class _State:
    """Blend off, depth test off and depth write off, restored on exit.

    Face culling is also turned off and colour writes on, and both stay
    that way on exit: `gpu.state` cannot read either.
    """

    def __enter__(self):
        self.blend = gpu.state.blend_get()
        self.depth_test = gpu.state.depth_test_get()
        self.depth_mask = gpu.state.depth_mask_get()
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        # `gpu.state` has no getter for these, so they are forced and left
        # at Blender's defaults afterwards.
        gpu.state.face_culling_set('NONE')
        gpu.state.color_mask_set(True, True, True, True)
        return self

    def __exit__(self, *exc):
        gpu.state.blend_set(self.blend)
        gpu.state.depth_test_set(self.depth_test)
        gpu.state.depth_mask_set(self.depth_mask)
        return False


class OpSpec:
    """The part of an op a pass reads, in UV coordinates.

    `PaintSystemSelectionOp` is not needed to render, so tests and the
    self-test describe ops with this instead.
    """

    __slots__ = ('kind', 'mode', 'feather', 'antialias', 'points')

    def __init__(self, kind: str, mode: str = 'REPLACE', feather: float = 0.0,
                 antialias: bool = True, points=()):
        self.kind = kind
        self.mode = mode
        self.feather = float(feather)
        self.antialias = bool(antialias)
        self.points = np.asarray(points, dtype=np.float64).reshape(-1, 2)

    @classmethod
    def from_op(cls, op) -> "OpSpec":
        """The spec of a `PaintSystemSelectionOp`, reading its points without a copy per value.

        Malformed points, which `get_mask` reports before it builds, read
        as no points.
        """
        raw = op.get(POINTS_KEY)
        view = points_view(raw) if raw is not None else None
        if view is None or not len(view):
            points = np.empty((0, 2))
        else:
            if view.format == 'd':
                flat = np.frombuffer(view, dtype=np.float64)
            else:
                # A list assigned with integers is stored as an int array.
                flat = np.asarray(view.tolist(), dtype=np.float64)
            points = flat.reshape(-1, 2).copy()
        return cls(op.kind, op.mode, op.feather, op.antialias, points)


def _half_width(feather: float, antialias: bool) -> float:
    """Half the soft edge width in texels, with the feather clamped to `FEATHER_MAX`."""
    feather = min(max(feather, 0.0), FEATHER_MAX) if feather == feather else 0.0
    return 0.5 * max(feather, 1.0 if antialias else 0.0)


def _float_texture(values: np.ndarray, fmt: str, width: int) -> gpu.types.GPUTexture:
    """A float texture *width* texels wide holding *values* row by row."""
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    channels = {'R32F': 1, 'RG32F': 2, 'RGBA32F': 4}[fmt]
    height = len(flat) // (width * channels)
    return gpu.types.GPUTexture((width, height), format=fmt, data=gpu.types.Buffer('FLOAT', len(flat), flat))


def _run_pass(spec: OpSpec, source, target, width: int, height: int, tile: int) -> None:
    """Draw one op into *target*, combining with *source* (None for an empty mask).

    Raises `outline.OutlineTooComplex` for a lasso past the table limits.
    """
    res = _resources()
    placeholder = res["placeholder"]
    half_width = _half_width(spec.feather, spec.antialias)
    texels = (spec.points - core.tile_offset(tile)) * (width, height)
    ints = {"mode": _MODE_UNIFORMS.get(spec.mode, 0), "use_previous": 0 if source is None else 1}
    floats = {"target_size": (float(width), float(height)), "half_width": half_width}
    samplers = {"previous": placeholder if source is None else source}

    edges = None
    if spec.kind == 'LASSO' and np.isfinite(texels).all():
        edges = outline.closed_outline(texels)
    if edges is not None:
        a, b = edges
        spans, keys = outline.parity_tables(a, b, width, height)
        samplers["row_spans"] = _float_texture(spans, 'RG32F', spans.shape[1])
        samplers["keys"] = _float_texture(keys, 'R32F', outline.DATA_WIDTH)
        cell = outline.cell_size(half_width)
        if half_width > 0.0:
            cells, entries, bounds = outline.distance_tables(a, b, width, height, half_width, cell)
            samplers["cells"] = _float_texture(cells, 'RG32F', cells.shape[1])
            samplers["bounds"] = _float_texture(bounds, 'R32F', outline.DATA_WIDTH)
            samplers["entries"] = _float_texture(entries, 'RGBA32F', outline.DATA_WIDTH)
        else:
            samplers["cells"] = samplers["bounds"] = samplers["entries"] = placeholder
        ints.update(span_width=outline.SPAN_WIDTH, cell_size=cell)
        shader, batch = res["lasso"]
    else:
        # A lasso that encloses nothing draws as the empty shape.
        shape = {'ALL': _SHAPE_ALL, 'BOX': _SHAPE_BOX, 'ELLIPSE': _SHAPE_ELLIPSE,
                 'INVERT': _SHAPE_INVERT}.get(spec.kind, _SHAPE_NOTHING)
        values = np.zeros(4)
        if spec.kind in ('BOX', 'ELLIPSE'):
            if len(texels) >= 2 and np.isfinite(texels[:2]).all():
                low = np.minimum(texels[0], texels[1])
                high = np.maximum(texels[0], texels[1])
            else:
                low = high = np.zeros(2)
            if spec.kind == 'BOX':
                # Beyond the target by more than the soft edge, a box edge
                # changes nothing, so clamping keeps the values small.
                reach = half_width + 1.0
                size = np.array((width, height), dtype=np.float64)
                low = np.clip(low, -reach, size + reach)
                high = np.clip(high, -reach, size + reach)
                values = np.concatenate((low, high))
                if np.any(high <= low):
                    shape = _SHAPE_NOTHING
            else:
                radii = 0.5 * (high - low)
                values = np.concatenate((0.5 * (low + high), radii))
                if np.any(radii < MIN_RADIUS):
                    shape = _SHAPE_NOTHING
        whole = np.floor(values)
        ints["shape"] = shape
        floats["shape_whole"] = tuple(float(value) for value in whole)
        floats["shape_fraction"] = tuple(float(value) for value in values - whole)
        shader, batch = res["shape"]

    framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
    sync = gpu.types.Buffer('FLOAT', 1)
    with framebuffer.bind():
        for first in range(0, height, BAND_ROWS):
            last = min(height, first + BAND_ROWS)
            for name, value in ints.items():
                shader.uniform_int(name, value)
            for name, value in floats.items():
                shader.uniform_float(name, value)
            for name, texture in samplers.items():
                shader.uniform_sampler(name, texture)
            shader.uniform_float("rows", (float(first), float(last)))
            batch.draw(shader)
            if last < height:
                # Reading a texel waits for the band to finish.
                framebuffer.read_color(0, first, 1, 1, 1, 0, 'FLOAT', data=sync)


def _run_chain(specs, source, targets, width: int, height: int, tile: int) -> None:
    """Run *specs* in order from *source*, the last into ``targets[0]``.

    With two or more specs, the one before the last lands in
    ``targets[1]``. A lasso past the table limits raises `MaskUnavailable`
    with reason `TOO_COMPLEX` and its index into *specs*.
    """
    passes = len(specs)
    failed_step = None
    with _State():
        for step, spec in enumerate(specs):
            target = targets[(passes - 1 - step) % 2] if passes >= 2 else targets[0]
            try:
                _run_pass(spec, source, target, width, height, tile)
            except outline.OutlineTooComplex:
                failed_step = step
                break
            source = target
    if failed_step is not None:
        # Raised outside the except block and with the textures dropped:
        # a traceback keeps every frame's locals alive, and a held
        # exception would otherwise keep the textures past `release()`.
        source = target = targets = None
        raise MaskUnavailable('TOO_COMPLEX', _MESSAGES['TOO_COMPLEX'], failed_step)


# ── Self-test ────────────────────────────────────────────────────────

SELF_TEST_OPS = (
    OpSpec('BOX', 'REPLACE', 8.0, True, [(0.25, 0.125), (0.75, 0.5)]),
    OpSpec('ELLIPSE', 'ADD', 16.0, True, [(20.25 / 64, 36.25 / 64), (44.75 / 64, 60.75 / 64)]),
    OpSpec('LASSO', 'SUBTRACT', 4.0, True,
           [(28.25 / 64, 4.25 / 64), (60.25 / 64, 4.25 / 64), (60.25 / 64, 36.25 / 64)]),
    OpSpec('LASSO', 'ADD', 0.0, False, [(2 / 64, 40 / 64), (12 / 64, 40 / 64), (12 / 64, 62 / 64), (2 / 64, 62 / 64)]),
    OpSpec('INVERT', 'ADD'),
    OpSpec('BOX', 'INTERSECT', 0.0, True, [(0.0, 0.0), (1.0, 0.96875)]),
)
"""A 64 x 64 chain: a box feathered by 8 texels, a circle feathered by 16
that it adds, a triangle lasso feathered by 4 that it subtracts, a hard
square lasso that it adds, an inversion and an anti-aliased box that it
intersects. `SELF_TEST_EXPECTED` checks the soft edge profile, the
ellipse's early out and its root, the lasso distance tables over a
2 x 2 grid of 32-texel cells, the hard branch of the lasso pass, all four
modes, `INVERT`, and an `ADD` of fractional coverage to a fractional
mask, where `max` differs from a sum. Every lasso table fits in one span
column and one data texture row."""

SELF_TEST_EXPECTED = {
    (12, 16): 0.98876953125,
    (17, 16): 0.23193359375,
    (20, 16): 0.0,
    (20, 40): 0.6986395918352175,
    (47, 48): 0.7476577758789062,
    (43, 57): 0.6803087713210262,
    (40, 60): 0.6986395918352175,
    (30, 10): 0.09228515625,
    (44, 12): 1.0,
    (58, 30): 1.0,
    (5, 45): 0.0,
    (12, 50): 0.9997371090224711,
    (1, 50): 1.0,
    (32, 62): 0.0,
    (32, 61): 0.5701065063476562,
    (63, 0): 1.0,
    (33, 33): 0.7504059740217613,
}
"""Texel (x, y) to the value of `SELF_TEST_OPS` there, computed in float64
by `tests/selection_reference.py`. At (33, 33) the circle adds coverage
0.2496 to the box's 0.2319."""


def _self_test_comb() -> list[tuple[float, float]]:
    """The outline of the comb lasso in `SELF_TEST_WIDE_OPS`, in UV.

    33 teeth 2 texels wide, one every 4 texels from x = 3.3, each running
    from below the bottom row to above the top row and joined below the
    target. The 133 points cross each of the 64 rows 66 times.
    """
    width, height = SELF_TEST_WIDE_SIZE
    teeth = 33
    points = [(3.3, -2.0)]
    for tooth in range(teeth):
        left = 3.3 + 4.0 * tooth
        points += [(left, 66.0), (left + 2.0, 66.0), (left + 2.0, -1.0)]
        points.append((left + 4.0, -1.0) if tooth < teeth - 1 else (left + 2.0, -2.0))
    return [(x / width, y / height) for x, y in points]


SELF_TEST_WIDE_OPS = (
    OpSpec('ALL'),
    OpSpec('BOX', 'SUBTRACT', 0.0, False, [(20.25 / 140, 10.25 / 64), (60.75 / 140, 30.75 / 64)]),
    OpSpec('LASSO', 'INTERSECT', 0.0, False, _self_test_comb()),
)
"""A chain on `SELF_TEST_WIDE_SIZE`: everything selected, a hard box that
it subtracts and a hard comb lasso that it intersects.
`SELF_TEST_WIDE_EXPECTED` checks `ALL`, the hard branch of the edge
profile, and the lasso parity tables beyond one span column and one data
texture row: the comb's 4224 crossings fill two rows of the key texture,
and its teeth run through all three span columns and straddle their
boundaries, so the span column lookup and the parity carried into a span
are both used."""

SELF_TEST_WIDE_EXPECTED = {
    (5, 1): 0.0, (7, 1): 1.0, (29, 1): 0.0, (31, 1): 1.0, (69, 1): 0.0,
    (71, 1): 1.0, (101, 1): 0.0, (103, 1): 1.0, (133, 1): 0.0, (135, 1): 0.0,
    (5, 20): 0.0, (7, 20): 1.0, (29, 20): 0.0, (31, 20): 0.0, (69, 20): 0.0,
    (71, 20): 1.0, (101, 20): 0.0, (103, 20): 1.0, (133, 20): 0.0, (135, 20): 0.0,
    (5, 63): 0.0, (7, 63): 1.0, (29, 63): 0.0, (31, 63): 1.0, (69, 63): 0.0,
    (71, 63): 1.0, (101, 63): 0.0, (103, 63): 1.0, (133, 63): 0.0, (135, 63): 0.0,
}
"""Texel (x, y) to the value of `SELF_TEST_WIDE_OPS` there, computed in
float64 by `tests/selection_reference.py`."""

_self_test_result: bool | None = None


def self_test() -> bool | None:
    """Whether this GPU draws masks correctly. Run once per session, on the first build.

    Renders `SELF_TEST_OPS` and `SELF_TEST_WIDE_OPS`, and passes only when
    both match their expected texels within `SELF_TEST_TOLERANCE`. A
    driver that gets a path those chains check wrong fails here, and
    every later build raises `SELF_TEST` rather than handing tools a wrong
    mask. Neither chain checks a hard ellipse, distance cells wider than
    32 texels, distance tables past one data texture row or a tile other
    than 1001.

    None when the self-test could not run because the GPU raised
    `RuntimeError`, as it does with no active context. None is not
    memoised, so the next build runs the self-test again. Any other
    exception counts as a failure.
    """
    global _self_test_result
    if _self_test_result is None:
        chains = ((SELF_TEST_OPS, (SELF_TEST_SIZE, SELF_TEST_SIZE), SELF_TEST_EXPECTED),
                  (SELF_TEST_WIDE_OPS, SELF_TEST_WIDE_SIZE, SELF_TEST_WIDE_EXPECTED))
        failed = {}
        try:
            for specs, (width, height), expected in chains:
                got = render(specs, width, height)
                for (x, y), value in expected.items():
                    off = abs(float(got[y, x]) - value)
                    if not off <= SELF_TEST_TOLERANCE:
                        failed[f"({x}, {y}) of {width} x {height}"] = off
        except MaskUnavailable:
            raise
        except RuntimeError as error:
            # gpu.types raises RuntimeError when no GPU context is active,
            # as in a load_post handler on Blender 5.3, or when an allocation
            # fails. Neither shows that the GPU draws wrongly, so the result
            # is not memoised and the next build runs the self-test again.
            log.warning("The selection self-test could not run and will be retried: %s", str(error))
            return None
        except Exception:
            log.exception("The selection self-test could not run")
            _self_test_result = False
            return False
        _self_test_result = not failed
        if failed:
            log.error("Selection self-test failed on %s: texels off by more than %g: %s",
                      gpu.platform.renderer_get(), SELF_TEST_TOLERANCE, failed)
    return _self_test_result


def render(specs, width: int, height: int, tile: int = 1001) -> np.ndarray:
    """Run *specs* from an empty mask and read the result, float32 `(height, width)`.

    Uncached and unchecked by the self-test; for the self-test itself and
    for tests. Raises `MaskUnavailable` for `NO_GPU`, `NO_SIZE`,
    `TOO_LARGE`, `UNSUPPORTED` (index into *specs*) and `TOO_COMPLEX`.
    """
    problem = _target_problem(width, height)
    if problem is not None:
        raise MaskUnavailable(problem, _MESSAGES[problem])
    for index, spec in enumerate(specs):
        if spec.kind not in SUPPORTED_KINDS:
            message = _UNSUPPORTED_KIND_MESSAGES.get(spec.kind, "This selection operation cannot be built")
            raise MaskUnavailable('UNSUPPORTED', message, index)
    specs = list(specs)
    if not specs:
        return np.zeros((height, width), dtype=np.float32)
    targets = [gpu.types.GPUTexture((width, height), format='R32F') for _ in range(min(len(specs), 2))]
    try:
        _run_chain(specs, None, targets, width, height, tile)
        return SelectionMask(targets[0], width, height, tile, b"").read()
    finally:
        # See `_run_chain`: a raised error must not keep the targets alive.
        targets = None


# ── Cache ────────────────────────────────────────────────────────────

_masks: dict[bytes, SelectionMask] = {}  # insertion order is recency, oldest first
_pool: list[tuple[tuple[int, int], gpu.types.GPUTexture]] = []
_warned: set = set()
_stats = dict(builds=0, passes=0, hits=0, allocations=0, evictions=0)


def mask_size(selection, size: tuple[int, int] | None = None, tile: int = 1001) -> tuple[int, int]:
    """The size a mask of *selection* is built at: *size*, else the image's.

    For a tiled image that is the size of tile *tile*, which is (0, 0)
    when the image has no such tile or the tile has no pixels. A missing
    image file also reports (0, 0).
    """
    if size is not None:
        return int(size[0]), int(size[1])
    image = selection.image
    if image is None:
        return 0, 0
    if image.source == 'TILED':
        for image_tile in image.tiles:
            if image_tile.number == tile:
                return int(image_tile.size[0]), int(image_tile.size[1])
        return 0, 0
    return int(image.size[0]), int(image.size[1])


def _target_problem(width: int, height: int, probe: bool = True) -> str | None:
    """Why no mask can be built at this size, as a reason, or None.

    With *probe* False, a background GPU context that has not been started
    is not started for the answer. The size is then checked against
    `MAX_TEXELS` only, because the maximum texture size needs a context.
    """
    ready = core.gpu_available() if probe else core.gpu_known()
    if ready is False:
        return 'NO_GPU'
    if width <= 0 or height <= 0:
        return 'NO_SIZE'
    if width * height > MAX_TEXELS:
        return 'TOO_LARGE'
    if ready and max(width, height) > gpu.capabilities.max_texture_size_get():
        return 'TOO_LARGE'
    return None


def _problem(selection, width: int, height: int, probe: bool = True) -> tuple[str, str, int] | None:
    """Why *selection* cannot be built at this size, as (reason, message, op index).

    *probe* is passed to `_target_problem`.
    """
    reason = _target_problem(width, height, probe)
    if reason is not None:
        return reason, _MESSAGES[reason], -1
    ops = selection.ops
    for index in range(selection.chain_start(), len(ops)):
        op = ops[index]
        if op.kind not in SUPPORTED_KINDS:
            return 'UNSUPPORTED', _UNSUPPORTED_KIND_MESSAGES[op.kind], index
        if op.space == 'VIEW' and op.kind not in SPACELESS_KINDS:
            return 'UNSUPPORTED', _MESSAGES['VIEW'], index
        if op.kind not in OUTLINELESS_KINDS:
            raw = op.get(POINTS_KEY)
            if raw is not None and points_view(raw) is None:
                return 'UNSUPPORTED', _MESSAGES['POINTS'], index
    if _self_test_result is False:
        return 'SELF_TEST', _MESSAGES['SELF_TEST'], -1
    return None


def _raise(problem: tuple[str, str, int], key: bytes):
    """Raise *problem*, logging it once per reason and selection state."""
    reason, message, index = problem
    if (reason, key) not in _warned:
        if len(_warned) >= 256:
            _warned.clear()
        _warned.add((reason, key))
        log.warning("Selection mask unavailable: %s", message)
    raise MaskUnavailable(reason, message, index)


def availability(selection, size: tuple[int, int] | None = None, tile: int = 1001) -> str:
    """Empty when `get_mask` can build *selection*, else the reason as a UI message.

    An empty selection is available. Cheap enough for a `poll` or a draw
    callback: it builds nothing and does not run the self-test, so a GPU
    that has not been tested yet reports available. Likewise it does not
    start a background GPU context, so a background session of Blender
    5.2 or later whose context has not been started yet reports available.
    """
    if not len(selection.ops):
        return ""
    width, height = mask_size(selection, size, tile)
    problem = _problem(selection, width, height, probe=False)
    return problem[1] if problem is not None else ""


def _touch(key: bytes) -> SelectionMask:
    mask = _masks.pop(key)
    _masks[key] = mask
    return mask


def _cached_bytes() -> int:
    return sum(mask.video_memory for mask in _masks.values())


def _evict(reserve: int, keep: set, width: int, height: int) -> None:
    """Evict the least recently used masks until *reserve* more bytes fit the budget.

    Masks in *keep* stay. Evicted textures of the size being built go to
    the pool for the build to reuse.
    """
    total = _cached_bytes()
    for key in list(_masks):
        if total + reserve <= CACHE_BUDGET:
            break
        if key in keep:
            continue
        mask = _masks.pop(key)
        total -= mask.video_memory
        mask.alive = False
        _stats["evictions"] += 1
        if mask.size == (width, height) and len(_pool) < POOL_LIMIT:
            _pool.append((mask.size, mask._texture))
        mask._texture = None


def _acquire(width: int, height: int) -> gpu.types.GPUTexture:
    """A pooled texture of this size, or a new one. A new size empties the pool."""
    for index, (size, texture) in enumerate(_pool):
        if size == (width, height):
            del _pool[index]
            return texture
    _pool.clear()
    _stats["allocations"] += 1
    return gpu.types.GPUTexture((width, height), format='R32F')


def _return_to_pool(targets, width: int, height: int) -> None:
    """Pool the targets of a failed build, for the next build of this size to reuse."""
    for texture in targets:
        if len(_pool) < POOL_LIMIT:
            _pool.append(((width, height), texture))


def peek_mask(selection, size: tuple[int, int] | None = None, tile: int = 1001) -> SelectionMask | None:
    """The cached mask of *selection*, or None. Never builds and never raises.

    For draw callbacks, which must not run passes: they show what is
    cached and a tool or timer calls `get_mask`.
    """
    if not len(selection.ops):
        return None
    width, height = mask_size(selection, size, tile)
    if width <= 0 or height <= 0:
        return None
    key = selection.prefix_digests(width, height, tile)[-1]
    if key not in _masks:
        return None
    return _touch(key)


def get_mask(selection, size: tuple[int, int] | None = None, tile: int = 1001) -> SelectionMask | None:
    """The mask of *selection*, built or taken from the cache.

    *size* overrides the size of `selection.image`; *tile* picks the UDIM
    tile, whose UV square maps onto the mask. Returns None for an empty
    selection and raises `MaskUnavailable` when the mask cannot be built;
    the first failure for a given selection state is logged as a warning.
    Ops before the last `REPLACE` of a kind in `REPLACING_KINDS` do not
    change the cache key and cost no pass.

    Must be called with a GPU context, as from an operator, a timer or a
    draw callback. Do not keep the result: the next call may evict it.
    """
    ops = selection.ops
    if not len(ops):
        return None
    width, height = mask_size(selection, size, tile)
    problem = _problem(selection, width, height)
    if problem is not None:
        _raise(problem, selection.prefix_digests(max(width, 0), max(height, 0), tile)[-1])
    digests = selection.prefix_digests(width, height, tile)
    last = len(ops) - 1
    if digests[last] in _masks:
        _stats["hits"] += 1
        return _touch(digests[last])
    passed = self_test()
    if passed is None:
        _raise(('GPU_ERROR', _MESSAGES['GPU_ERROR'], -1), digests[last])
    if not passed:
        _raise(('SELF_TEST', _MESSAGES['SELF_TEST'], -1), digests[last])

    start = selection.chain_start()
    source_index = None
    for index in range(last - 1, start - 1, -1):
        if digests[index] in _masks:
            source_index = index
            break
    first = start if source_index is None else source_index + 1
    specs = [OpSpec.from_op(ops[index]) for index in range(first, last + 1)]
    passes = len(specs)
    keep = set() if source_index is None else {digests[source_index]}
    _evict(min(passes, 2) * width * height * 4, keep, width, height)
    source = None if source_index is None else _touch(digests[source_index]).texture
    targets = []
    failure = None
    _stats["builds"] += 1
    try:
        for _ in range(min(passes, 2)):
            targets.append(_acquire(width, height))
        _run_chain(specs, source, targets, width, height, tile)
    except MaskUnavailable as error:
        failure = (error.reason, str(error), first + error.op_index)
    except RuntimeError as error:
        # gpu.types raises RuntimeError when no GPU context is active or an
        # allocation fails; a later build may succeed.
        log.debug("Selection mask build failed: %s", str(error))
        failure = ('GPU_ERROR', _MESSAGES['GPU_ERROR'], -1)
    if failure is not None:
        _return_to_pool(targets, width, height)
        # See `_run_chain`: nothing the traceback keeps may hold a texture.
        source = targets = None
        _raise(failure, digests[last])
    _stats["passes"] += passes
    if passes >= 2:
        _masks[digests[last - 1]] = SelectionMask(targets[1], width, height, tile, digests[last - 1])
    result = SelectionMask(targets[0], width, height, tile, digests[last])
    _masks[digests[last]] = result
    return result


def invalidate() -> None:
    """Drop every cached mask and pooled texture. Held masks die."""
    for mask in _masks.values():
        mask.alive = False
        mask._texture = None
    _masks.clear()
    _pool.clear()
    _warned.clear()


def release() -> None:
    """Free every GPU object this module holds, and forget the self-test.

    Called when the addon is unregistered, while the GPU context is still
    up: in a background session Python's own teardown runs after it has
    gone, and freeing a texture there segfaults Blender.
    """
    global _self_test_result
    invalidate()
    _gpu.clear()
    _self_test_result = None


def stats() -> dict:
    """Counters since `reset_stats`, plus what the cache holds now. For tests."""
    out = dict(_stats)
    out.update(cached=len(_masks), pooled=len(_pool),
               video_memory=_cached_bytes() + sum(w * h * 4 for (w, h), _ in _pool))
    return out


def reset_stats() -> None:
    """Zero the counters `stats` reports."""
    for key in _stats:
        _stats[key] = 0
