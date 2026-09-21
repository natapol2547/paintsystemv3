"""Selections drawn in the 3D view, rasterised into UV space (PS-093).

A `VIEW` op holds an outline in region pixels and the view it was drawn
in. `raster._run_pass` hands such an op to `run_view_pass`, which draws it
into the same `R32F` mask as a `UV` op, through the texel map of the
object it was drawn on (`gpu_passes/texel_map.py`). Three stages per op:

A. The outline's signed distance at region size, from raster's shape and
   lasso shaders compiled with `SIGNED_DISTANCE`, clamped to the soft
   edge's half width plus `REACH_PAD` pixels.
B. The view depth of the surface at region size, skipped with Through:
   `q` (`-1/d` in perspective, `d` in orthographic, both linear in screen
   space) and its screen slope, into an `RG32F` colour target with a
   `DEPTH_COMPONENT32F` depth target. Only the painted object is drawn,
   so only it can hide a texel.
C. One banded pass over the mask texels. Each texel's world position is
   projected into the region; the shape is a manual bilinear read of A,
   the facing test uses the geometric normal of the texel map, and the
   depth test is a 4-tap percentage-closer filter over B with a bias from
   both slopes. Margin texels (coverage at most `MARGIN_ALPHA`) skip the
   depth test, so the margin next to a selected island is selected with
   it. The result combines with the previous mask as a `UV` op's does.
   Texels that project outside the region are 0.

The op stores the object-to-view matrix at commit; `ViewSpec.from_op`
multiplies it by the inverse of the object's world matrix now, so the
selection stays on the texels it was drawn over when the object moves.
The eye comes from `view_eye`.

`raster.view_self_test` renders `self_test_chain` for the orthographic
and perspective scenes with and without Through against float64 values
from `tests/selection_reference.py`. It cannot catch dropping only one of
the two depth slope terms, `REACH_PAD`, or a smooth normal used for facing
in place of the geometric one; in perspective it also misses the `clip.w`
in-front test, an eye vector used as the eye position for facing and a
missing perspective divide in the texel slope. `view_eye` runs on the CPU
and is checked by the tests instead.
"""
import math

import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes import core, texel_map
from . import raster

REACH_PAD = 1.5
"""Pixels of signed distance kept beyond the soft edge, so the bilinear
read of stage A is exact within one pixel of it."""

DEPTH_REL_BIAS = 1e-5
"""Depth bias relative to the stored `q`."""

DEPTH_ABS_BIAS = 1e-6
"""Absolute depth bias, orthographic views only. In perspective `q` is
`-1/d`, where a fixed bias would grow with the square of the distance."""

TEXEL_SLOPE_CAP = 0.03
"""Largest texel slope term, relative to `|q|`, so a texel seen edge on
cannot pass the depth test from behind the surface."""

MARGIN_ALPHA = 0.75
"""Texel map coverage at or below which a texel is margin, not surface."""

TARGET_SETS = 2
"""Region target sets kept, one per region size, least recently used dropped."""

SELF_TEST_TEXELS = 32
"""Width and height of the self-test mask and texel map."""

SELF_TEST_REGION = (64, 64)
"""Region size of the self-test views, in pixels."""

FLOOR_TILT = -0.25
"""Slope of the self-test floor in z per unit x, so its depth has a screen slope."""

_BLOCK_TYPEDEF = """
struct PSViewBlock {
  mat4 view_projection;
  mat4 view;
  vec4 eye;
  vec4 region;
  vec4 bias;
};
"""

_DEPTH_VERTEX_SOURCE = """
void main()
{
  vec4 world = vec4(position, 1.0);
  v_depth = -(view_block.view * world).z;
  gl_Position = view_block.view_projection * world;
}
"""

_DEPTH_FRAGMENT_SOURCE = """
void main()
{
  float q = view_block.eye.w != 0.0 ? -1.0 / v_depth : v_depth;
  float slope = max(abs(dFdx(q)), abs(dFdy(q)));
  out_depth = vec4(q, slope, 0.0, 1.0);
}
"""

_TEXEL_FRAGMENT_SOURCE = """
float edge_profile(float s)
{
  float half_width = view_block.region.z;
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

/* Bilinear read of the signed distance at region pixel s. */
float screen_distance(vec2 s)
{
  ivec2 size = textureSize(distance_map, 0);
  vec2 f = s - 0.5;
  vec2 base = floor(f);
  vec2 t = f - base;
  ivec2 i0 = ivec2(base);
  ivec2 hi = size - 1;
  float d00 = texelFetch(distance_map, clamp(i0, ivec2(0), hi), 0).r;
  float d10 = texelFetch(distance_map, clamp(i0 + ivec2(1, 0), ivec2(0), hi), 0).r;
  float d01 = texelFetch(distance_map, clamp(i0 + ivec2(0, 1), ivec2(0), hi), 0).r;
  float d11 = texelFetch(distance_map, clamp(i0 + ivec2(1, 1), ivec2(0), hi), 0).r;
  return mix(mix(d00, d10, t.x), mix(d01, d11, t.x), t.y);
}

/* Fraction of the four depth pixels around s that q is not behind. Each
   pixel's plane is extended to s by its own slope plus the texel's. */
float visibility(vec2 s, float q, float texel_slope)
{
  ivec2 size = textureSize(depth_map, 0);
  vec2 f = s - 0.5;
  vec2 base = floor(f);
  vec2 t = f - base;
  ivec2 i0 = ivec2(base);
  ivec2 hi = size - 1;
  float result = 0.0;
  for (int j = 0; j < 2; j++) {
    for (int i = 0; i < 2; i++) {
      ivec2 at = i0 + ivec2(i, j);
      vec4 sample_value = texelFetch(depth_map, clamp(at, ivec2(0), hi), 0);
      vec2 offset = abs(vec2(at) + 0.5 - s);
      float slope = sample_value.g + min(texel_slope, view_block.bias.w * abs(q));
      float allowed = sample_value.r + slope * (offset.x + offset.y)
                      + view_block.bias.x * abs(sample_value.r) + view_block.bias.y;
      float weight = (i == 0 ? 1.0 - t.x : t.x) * (j == 0 ? 1.0 - t.y : t.y);
      result += q <= allowed ? weight : 0.0;
    }
  }
  return result;
}

/* The world step from p to its neighbours ahead and behind along one axis,
   preferring surface neighbours over margin ones and forward differences
   over backward. Zero when neither neighbour is covered. When both are
   surface, the step more nearly along the surface at p (normal n): at the
   edge of a UV island the texel beyond may lie on another face. */
vec3 surface_step(vec3 p, vec3 n, vec4 ahead, vec4 behind)
{
  vec3 forward = ahead.xyz - p;
  vec3 backward = p - behind.xyz;
  if (ahead.a > MARGIN_ALPHA && behind.a > MARGIN_ALPHA) {
    bool leaves_less = abs(dot(backward, n)) * length(forward) < abs(dot(forward, n)) * length(backward);
    return leaves_less ? backward : forward;
  }
  if (ahead.a > MARGIN_ALPHA) {
    return forward;
  }
  if (behind.a > MARGIN_ALPHA) {
    return backward;
  }
  if (ahead.a > 0.0) {
    return forward;
  }
  return behind.a > 0.0 ? backward : vec3(0.0);
}

/* The texel map at *texel*, uncovered outside the map. */
vec4 position_at(ivec2 texel)
{
  if (any(lessThan(texel, ivec2(0))) || any(greaterThanEqual(texel, textureSize(positions, 0)))) {
    return vec4(0.0);
  }
  return texelFetch(positions, texel, 0);
}

/* World steps to the next texel along x and y. */
void surface_steps(ivec2 texel, vec3 p, vec3 n, out vec3 dx, out vec3 dy)
{
  dx = surface_step(p, n, position_at(texel + ivec2(1, 0)), position_at(texel - ivec2(1, 0)));
  dy = surface_step(p, n, position_at(texel + ivec2(0, 1)), position_at(texel - ivec2(0, 1)));
}

float depth_q(vec3 p)
{
  float depth = -(view_block.view * vec4(p, 1.0)).z;
  return view_block.eye.w != 0.0 ? -1.0 / depth : depth;
}

vec2 screen_of(vec3 p)
{
  vec4 clip = view_block.view_projection * vec4(p, 1.0);
  return (clip.xy / clip.w * 0.5 + 0.5) * view_block.region.xy;
}

/* Largest screen slope of q across the texel's surface patch. */
float surface_slope(vec3 p, vec3 dx, vec3 dy, vec2 s, float q)
{
  vec2 su = screen_of(p + dx) - s;
  vec2 sv = screen_of(p + dy) - s;
  float qu = depth_q(p + dx) - q;
  float qv = depth_q(p + dy) - q;
  float det = su.x * sv.y - su.y * sv.x;
  if (abs(det) < 1e-12) {
    return 3.0e38;
  }
  float gx = (qu * sv.y - qv * su.y) / det;
  float gy = (qv * su.x - qu * sv.x) / det;
  return max(abs(gx), abs(gy));
}

/* The normal of the texel's patch, turned to the side of the smooth normal. */
vec3 geometric_normal(vec3 dx, vec3 dy, vec3 smooth_normal)
{
  vec3 n = cross(dx, dy);
  float len = length(n);
  if (len <= 1e-12) {
    return smooth_normal;
  }
  n /= len;
  return dot(n, smooth_normal) < 0.0 ? -n : n;
}

/* Shape coverage times facing times visibility for one texel. Through is
   bit 0 of flags. */
float texel_coverage(ivec2 texel)
{
  vec4 position_value = texelFetch(positions, texel, 0);
  if (position_value.a <= 0.0) {
    return 0.0;
  }
  vec3 p = position_value.xyz;
  vec4 clip = view_block.view_projection * vec4(p, 1.0);
  bool in_front = view_block.eye.w == 0.0 || clip.w > 0.0;
  vec3 ndc = clip.xyz / clip.w;
  vec2 s = (ndc.xy * 0.5 + 0.5) * view_block.region.xy;
  if (!in_front || abs(ndc.z) > 1.0 || any(lessThan(s, vec2(0.0)))
      || any(greaterThan(s, view_block.region.xy))) {
    return 0.0;
  }
  float shape_coverage = edge_profile(screen_distance(s));
  if (shape_coverage <= 0.0 || (flags & 1) != 0) {
    return shape_coverage;
  }
  vec3 smooth_normal = texelFetch(normals, texel, 0).xyz;
  vec3 to_eye = view_block.eye.xyz - p * view_block.eye.w;
  vec3 dx, dy;
  surface_steps(texel, p, smooth_normal, dx, dy);
  if (dot(geometric_normal(dx, dy, smooth_normal), to_eye) <= 0.0) {
    return 0.0;
  }
  if (position_value.a <= MARGIN_ALPHA) {
    return shape_coverage;
  }
  float q = depth_q(p);
  return shape_coverage * visibility(s, q, surface_slope(p, dx, dy, s, q));
}

void main()
{
  ivec2 texel = ivec2(floor(v_texel));
  float previous_value = (mode == 0 || use_previous == 0) ? 0.0 : texelFetch(previous, texel, 0).r;
  out_mask = combine(previous_value, texel_coverage(texel));
}
""".replace("MARGIN_ALPHA", repr(MARGIN_ALPHA))

_gpu: dict = {}
# Region size -> textures for stages A and B; insertion order is recency, oldest first.
_targets: dict[tuple[int, int], dict] = {}


def _shaders() -> dict:
    """The depth and texel shaders and the texel pass quad, built once per session."""
    if not _gpu:
        interface = gpu.types.GPUStageInterfaceInfo("ps_selection_view_depth_interface")
        interface.smooth('FLOAT', "v_depth")
        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source(_BLOCK_TYPEDEF)
        info.uniform_buf(0, "PSViewBlock", "view_block")
        info.vertex_in(0, 'VEC3', "position")
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', "out_depth")
        info.vertex_source(_DEPTH_VERTEX_SOURCE)
        info.fragment_source(_DEPTH_FRAGMENT_SOURCE)
        depth = gpu.shader.create_from_info(info)

        interface = gpu.types.GPUStageInterfaceInfo("ps_selection_view_texel_interface")
        interface.smooth('VEC2', "v_texel")
        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source(_BLOCK_TYPEDEF)
        info.uniform_buf(0, "PSViewBlock", "view_block")
        info.push_constant('VEC2', "target_size")
        info.push_constant('VEC2', "rows")
        info.push_constant('INT', "mode")
        info.push_constant('INT', "use_previous")
        info.push_constant('INT', "flags")
        info.sampler(0, 'FLOAT_2D', "previous")
        info.sampler(1, 'FLOAT_2D', "positions")
        info.sampler(2, 'FLOAT_2D', "normals")
        info.sampler(3, 'FLOAT_2D', "distance_map")
        info.sampler(4, 'FLOAT_2D', "depth_map")
        info.vertex_in(0, 'VEC2', "position")
        info.vertex_out(interface)
        info.fragment_out(0, 'FLOAT', "out_mask")
        info.vertex_source(core.BAND_VERTEX_SOURCE)
        info.fragment_source(_TEXEL_FRAGMENT_SOURCE)
        texel = gpu.shader.create_from_info(info)
        _gpu.update(depth=depth, texel=(texel, batch_for_shader(texel, 'TRIS', core.UNIT_QUAD)))
    return _gpu


def is_orthographic(projection) -> bool:
    """Whether *projection* (4 x 4, rows first) has no perspective divide."""
    return bool(np.allclose(np.asarray(projection, dtype=np.float64).reshape(4, 4)[3], (0.0, 0.0, 0.0, 1.0)))


def view_eye(view, projection) -> np.ndarray:
    """Where the viewer is, as float64 `(x, y, z, w)` in world space.

    In perspective the eye position with w 1: the translation of the
    inverse view. In orthographic the unit direction towards the viewer
    with w 0: column 2 of the inverse view. Row 2 of the view is that
    direction only while the view's 3 x 3 part is rigid, which an object
    scaled after the op was drawn breaks.
    """
    inverse = np.linalg.inv(np.asarray(view, dtype=np.float64).reshape(4, 4))
    if is_orthographic(projection):
        direction = inverse[:3, 2]
        return np.append(direction / np.linalg.norm(direction), 0.0)
    return np.append(inverse[:3, 3], 1.0)


class ObjectSurface:
    """The evaluated mesh of an object, through its texel map and position batch."""

    __slots__ = ('object', 'uv_map')

    def __init__(self, obj, uv_map: str):
        self.object = obj
        self.uv_map = uv_map

    def texel_map(self, width: int, height: int, tile: int):
        return texel_map.get_texel_map(self.object, self.uv_map, (width, height), tile, fallback_to_active=False)

    def batch(self):
        return texel_map.get_position_batch(self.object, self.uv_map, fallback_to_active=False)


class SyntheticSurface:
    """A texel map and a triangle soup given as arrays, for the self-test."""

    __slots__ = ('position', 'normal', 'triangles')

    def __init__(self, positions: np.ndarray, normals: np.ndarray, triangles: np.ndarray):
        height, width = positions.shape[:2]
        flat = np.ascontiguousarray(positions, dtype=np.float32).ravel()
        self.position = gpu.types.GPUTexture((width, height), format='RGBA32F',
                                             data=gpu.types.Buffer('FLOAT', len(flat), flat))
        flat = np.ascontiguousarray(normals, dtype=np.float32).ravel()
        self.normal = gpu.types.GPUTexture((width, height), format='RGBA16F',
                                           data=gpu.types.Buffer('FLOAT', len(flat), flat))
        self.triangles = np.ascontiguousarray(triangles, dtype=np.float32).reshape(-1, 3)

    def texel_map(self, width: int, height: int, tile: int):
        return self

    def batch(self):
        return batch_for_shader(_shaders()["depth"], 'TRIS', {"position": self.triangles})


class ViewSpec:
    """The view part of a `VIEW` op: the surface, the region and the matrices at build time.

    `view` maps world space to view space and `projection` view space to
    clip space, both float64 4 x 4 with rows first.
    """

    __slots__ = ('surface', 'region', 'view', 'projection', 'through')

    def __init__(self, surface, region, view, projection, through: bool):
        self.surface = surface
        self.region = (int(region[0]), int(region[1]))
        self.view = np.asarray(view, dtype=np.float64).reshape(4, 4)
        self.projection = np.asarray(projection, dtype=np.float64).reshape(4, 4)
        self.through = bool(through)

    @classmethod
    def from_op(cls, op) -> "ViewSpec":
        """The spec of an outlined `VIEW` op whose object `raster._problem` accepted.

        The stored object-to-view matrix times the inverse of the object's
        world matrix now.
        """
        obj = op.object
        stored = np.array(op.view_matrix, dtype=np.float64)
        world = np.array(obj.matrix_world, dtype=np.float64)
        return cls(ObjectSurface(obj, op.uv_map), op.region_size, stored @ np.linalg.inv(world),
                   np.array(op.projection_matrix, dtype=np.float64), op.through)


def view_block(spec: ViewSpec, half_width: float, reach: float) -> gpu.types.GPUUniformBuf:
    """The `PSViewBlock` uniform buffer for *spec*: 176 bytes, std140."""
    orthographic = is_orthographic(spec.projection)
    data = b"".join((
        (spec.projection @ spec.view).T.astype(np.float32).tobytes(),
        spec.view.T.astype(np.float32).tobytes(),
        view_eye(spec.view, spec.projection).astype(np.float32).tobytes(),
        np.array((spec.region[0], spec.region[1], half_width, reach), dtype=np.float32).tobytes(),
        np.array((DEPTH_REL_BIAS, DEPTH_ABS_BIAS if orthographic else 0.0, 1.0, TEXEL_SLOPE_CAP),
                 dtype=np.float32).tobytes(),
    ))
    return gpu.types.GPUUniformBuf(data)


def _region_targets(region: tuple[int, int]) -> dict:
    """The stage A and B textures for *region*, kept for `TARGET_SETS` sizes.

    Textures only: a framebuffer works only in the context that created
    it, so each build makes its own.
    """
    targets = _targets.pop(region, None)
    if targets is None:
        while len(_targets) >= TARGET_SETS:
            del _targets[next(iter(_targets))]
        targets = dict(
            distance=gpu.types.GPUTexture(region, format='R32F'),
            colour=gpu.types.GPUTexture(region, format='RG32F'),
            depth=gpu.types.GPUTexture(region, format='DEPTH_COMPONENT32F'),
        )
    _targets[region] = targets
    return targets


def run_view_pass(spec: "raster.OpSpec", source, target, width: int, height: int, tile: int) -> None:
    """Draw the `VIEW` op *spec* into *target*, combining with *source* (None for an empty mask).

    Called by `raster._run_pass` inside its GPU state. Raises
    `raster.MaskUnavailable` with reason `SURFACE` when the surface has
    no texel map or position batch, and `outline.OutlineTooComplex` for a
    lasso past the table limits.
    """
    view = spec.view
    surface_map = view.surface.texel_map(width, height, tile)
    if surface_map is None:
        raise raster.MaskUnavailable('SURFACE', raster.MESSAGES['SURFACE'])
    batch = None if view.through else view.surface.batch()
    if not view.through and batch is None:
        raise raster.MaskUnavailable('SURFACE', raster.MESSAGES['SURFACE'])
    region_width, region_height = view.region
    targets = _region_targets(view.region)
    half_width = raster._half_width(spec.feather, spec.antialias)
    reach = half_width + REACH_PAD
    raster._run_distance_pass(spec, targets["distance"], region_width, region_height, reach)
    block = view_block(view, half_width, reach)
    shaders = _shaders()
    placeholder = core.unused_sampler()

    depth_map = placeholder
    if batch is not None:
        shader = shaders["depth"]
        framebuffer = gpu.types.GPUFrameBuffer(depth_slot=targets["depth"], color_slots=(targets["colour"],))
        depth_test = gpu.state.depth_test_get()
        depth_mask = gpu.state.depth_mask_get()
        try:
            with framebuffer.bind():
                framebuffer.clear(color=(3.0e38, 0.0, 0.0, 0.0), depth=1.0)
                gpu.state.depth_test_set('LESS')
                gpu.state.depth_mask_set(True)
                shader.bind()
                shader.uniform_block("view_block", block)
                batch.draw(shader)
        finally:
            gpu.state.depth_test_set(depth_test)
            gpu.state.depth_mask_set(depth_mask)
        depth_map = targets["colour"]

    shader, quad = shaders["texel"]
    ints = {"mode": raster._MODE_UNIFORMS.get(spec.mode, 0), "use_previous": 0 if source is None else 1,
            "flags": 1 if view.through else 0}
    samplers = {"previous": placeholder if source is None else source, "positions": surface_map.position,
                "normals": surface_map.normal, "distance_map": targets["distance"], "depth_map": depth_map}
    def draw_band(first, last):
        shader.bind()
        shader.uniform_block("view_block", block)
        for name, value in ints.items():
            shader.uniform_int(name, value)
        for name, texture in samplers.items():
            shader.uniform_sampler(name, texture)
        shader.uniform_float("target_size", (float(width), float(height)))
        shader.uniform_float("rows", (float(first), float(last)))
        quad.draw(shader)

    core.draw_in_bands(gpu.types.GPUFrameBuffer(color_slots=(target,)), height, draw_band)


# ── Self-test ────────────────────────────────────────────────────────


def self_test_scene(perspective: bool) -> dict:
    """The self-test surface and view, float64.

    Four strips of texels, each row one island: a floor tilted by
    `FLOOR_TILT`, an occluder above the middle third of the floor, a quad
    whose smooth normal faces away, and a margin strip (coverage 0.5)
    under the floor and the occluder. `triangles` is the depth soup
    (floor, occluder and the back quad). The orthographic view looks down
    -z with the scene square filling the region; the perspective view is
    rotated and 1.50 to 2.27 units from the islands.

    Returns `positions` and `normals` `(32, 32, 4)` with coverage in
    alpha, `triangles` `(n, 3)`, `view`, `projection`, and `labels`, the
    island name of each texel ('' for none).
    """
    n = SELF_TEST_TEXELS
    positions = np.zeros((n, n, 4))
    normals = np.zeros((n, n, 4))
    labels = np.full((n, n), '', dtype=object)
    column = np.arange(n)

    def strip(rows, xs, ys, z, normal_z, alpha, label):
        for index, row in enumerate(rows):
            positions[row, :, 0] = xs
            positions[row, :, 1] = ys[index]
            positions[row, :, 2] = z
            positions[row, :, 3] = alpha
            normals[row, :, 2] = normal_z
            normals[row, :, 3] = alpha
            labels[row, :] = label

    # Orthographic screen x is 2 * column + 1.25, so the bilinear reads mix 3:1.
    rows = range(0, 14)
    xs = (2 * column + 1.25) / 64
    strip(rows, xs, [(2 * row + 1.25) / 64 for row in rows], FLOOR_TILT * xs, 1.0, 1.0, 'floor')
    rows = range(15, 21)
    strip(rows, 1 / 3 + (column + 0.5) / 96, [(row - 15 + 0.5) / 6 * 0.4375 for row in rows], 0.5, 1.0, 1.0,
          'occluder')
    rows = range(22, 28)
    strip(rows, xs, [0.5 + (row - 22 + 0.5) / 6 * 0.25 for row in rows], 0.0, -1.0, 1.0, 'back')
    rows = range(29, 32)
    strip(rows, xs, [0.2 + (row - 29) * 0.01 for row in rows], 0.0, 1.0, 0.5, 'margin')

    def quad(x0, y0, x1, y1, z, tilt=0.0):
        return [(x, y, z + tilt * x) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1))]

    triangles = np.array(quad(0.0, 0.0, 1.0, 0.45, 0.0, FLOOR_TILT) + quad(1 / 3, 0.0, 2 / 3, 0.45, 0.5)
                         + quad(0.0, 0.5, 1.0, 0.75, 0.0))
    if perspective:
        angle, tilt, distance = 0.3, 0.12, 2.0
        near, far = 0.5, 20.0
        spin = np.eye(4)
        spin[:2, :2] = ((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle)))
        pitch = np.eye(4)
        pitch[1:3, 1:3] = ((math.cos(tilt), -math.sin(tilt)), (math.sin(tilt), math.cos(tilt)))
        centre = np.eye(4)
        centre[:3, 3] = (-0.5, -0.4, 0.0)
        back = np.eye(4)
        back[2, 3] = -distance
        view = back @ pitch @ spin @ centre
        projection = np.zeros((4, 4))
        projection[0, 0] = projection[1, 1] = 3.2
        projection[2, 2] = (far + near) / (near - far)
        projection[2, 3] = 2.0 * far * near / (near - far)
        projection[3, 2] = -1.0
    else:
        near, far = 1.0, 20.0
        view = np.eye(4)
        view[2, 3] = -10.0
        projection = np.eye(4)
        projection[0, 0] = projection[1, 1] = 2.0
        projection[0, 3] = projection[1, 3] = -1.0
        projection[2, 2] = -2.0 / (far - near)
        projection[2, 3] = -(far + near) / (far - near)
    return dict(positions=positions, normals=normals, triangles=triangles, view=view, projection=projection,
                labels=labels)


def self_test_ops() -> list[tuple]:
    """The self-test chain as (kind, mode, feather, antialias, points in region pixels).

    A box feathered by 8 that runs past the region's top and bottom, an
    anti-aliased ellipse it subtracts and a lasso feathered by 3 it adds.
    """
    return [
        ('BOX', 'REPLACE', 8.0, True, [(9.7, -30.0), (55.1, 80.0)]),
        ('ELLIPSE', 'SUBTRACT', 0.0, True, [(38.3, 3.1), (50.9, 22.7)]),
        ('LASSO', 'ADD', 3.0, True, [(3.3, 18.7), (29.6, 25.2), (15.9, 58.4)]),
    ]


def self_test_chain(through: bool, perspective: bool, band_rows: int = 16) -> np.ndarray:
    """Render the self-test chain from an empty mask, float32 `(32, 32)`."""
    scene = self_test_scene(perspective)
    surface = SyntheticSurface(scene["positions"], scene["normals"], scene["triangles"])
    view = ViewSpec(surface, SELF_TEST_REGION, scene["view"], scene["projection"], through)
    specs = [raster.OpSpec(kind, mode, feather, antialias, points, view=view)
             for kind, mode, feather, antialias, points in self_test_ops()]
    saved = core.BAND_ROWS
    core.BAND_ROWS = band_rows
    try:
        return raster.render(specs, SELF_TEST_TEXELS, SELF_TEST_TEXELS)
    finally:
        core.BAND_ROWS = saved
        # A raised error must not keep the textures alive (`raster._run_chain`).
        surface = view = specs = None


def release() -> None:
    """Free the shaders and region targets; `raster.release` calls it."""
    _gpu.clear()
    _targets.clear()
