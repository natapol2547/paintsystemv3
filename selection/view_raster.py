"""Selections drawn in the 3D view, rasterised into UV space (PS-093).

A `VIEW` op holds an outline in region pixels and the view it was drawn
in. `raster._run_pass` hands such an op to `run_view_pass`. That draws
it into the same `R32F` mask as a `UV` op, through the texel map of the
object it was drawn on (`gpu_passes/texel_map.py`). Each op runs three
stages.

A. The outline's signed distance at region size. It comes from raster's
   shape and lasso shaders compiled with `SIGNED_DISTANCE`. It is
   clamped to the soft edge's half width plus `REACH_PAD` pixels.
B. The view depth of the surface at region size. Through skips it. The
   pass writes `q` and its screen slope into an `RG32F` colour target,
   with a `DEPTH_COMPONENT32F` depth target. `q` is `-1/d` in
   perspective and `d` in orthographic, so it is linear in screen space
   either way. Only the painted object is drawn, so only it can hide a
   texel.
C. One banded pass over the mask texels. Each texel's world position is
   projected into the region. The shape is a manual bilinear read of A.
   The facing test uses the geometric normal of the texel map. The depth
   test is a 4-tap percentage-closer filter over B, with a bias from
   both slopes. Margin texels (coverage at most `MARGIN_ALPHA`) skip the
   depth test, so the margin next to a selected island is selected with
   it. Texels that project outside the region are 0. The result combines
   with the previous mask the same way a `UV` op's does.

The op stores the object-to-view matrix when it is committed.
`ViewSpec.from_op` multiplies it by the inverse of the object's current
world matrix. So the selection stays on the texels it was drawn over
when the object moves. The eye comes from `view_eye`.

`view_self_test` checks, once per session, that the GPU draws these
passes correctly. Its scene and expected values are in
`raster_selftest.py`, with the list of what it cannot catch.
"""
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes import core, texel_map
from . import raster
from .raster_glsl import COMMON_SOURCE

REACH_PAD = 1.5
"""Pixels of signed distance kept beyond the soft edge, so the bilinear
read of stage A is exact within one pixel of it."""

DEPTH_REL_BIAS = 1e-5
"""Depth bias relative to the stored `q`."""

DEPTH_ABS_BIAS = 1e-6
"""Absolute depth bias, orthographic views only. In perspective `q` is
`-1/d`, where a fixed bias would grow with the square of the distance."""

TEXEL_SLOPE_CAP = 0.03
"""Cap on the texel slope term, relative to `|q|`. It stops a texel seen
edge on from passing the depth test from behind the surface."""

MARGIN_ALPHA = 0.75
"""Texel map coverage at or below which a texel is margin, not surface."""

TARGET_SETS = 2
"""Region target sets kept, one per region size. The least recently used
is dropped first."""

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

/* Bilinear-weighted fraction of the four depth pixels around s that q is
   not behind. Each pixel's plane is extended to s by its own slope plus
   the texel's. */
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

/* The world step from p to one of its two neighbours along an axis.
   Surface neighbours win over margin ones, and the one ahead wins over
   the one behind. Zero when neither neighbour is covered. When both are
   surface, pick the step that stays closer to the surface at p (normal
   n). At the edge of a UV island, the texel beyond may lie on another
   face. */
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

/* The texel map value at texel, or all zero (uncovered) outside the map. */
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

/* The normal of the texel's patch, flipped to the side of the smooth
   normal. Falls back to the smooth normal for a degenerate patch. */
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
  float shape_coverage = edge_profile(screen_distance(s), view_block.region.z);
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
  float previous_value = mode == 0 ? 0.0 : previous_at(texel);
  out_mask = combine(previous_value, texel_coverage(texel));
}
""".replace("MARGIN_ALPHA", repr(MARGIN_ALPHA))

_gpu: dict = {}
# The textures for stages A and B, by region size. Insertion order is
# recency, oldest first.
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
        info.fragment_source(COMMON_SOURCE + _TEXEL_FRAGMENT_SOURCE)
        texel = gpu.shader.create_from_info(info)
        _gpu.update(depth=depth, texel=(texel, batch_for_shader(texel, 'TRIS', core.UNIT_QUAD)))
    return _gpu


def is_orthographic(projection) -> bool:
    """True when *projection* (4 x 4, rows first) has no perspective divide."""
    return bool(np.allclose(np.asarray(projection, dtype=np.float64).reshape(4, 4)[3], (0.0, 0.0, 0.0, 1.0)))


def view_eye(view, projection) -> np.ndarray:
    """Where the viewer is, as float64 `(x, y, z, w)` in world space.

    In perspective it is the eye position with w 1, which is the
    translation of the inverse view. In orthographic it is the unit
    direction towards the viewer with w 0, which is column 2 of the
    inverse view. Row 2 of the view is that direction only while the
    view's 3 x 3 part is rigid. An object scaled after the op was drawn
    breaks that.
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
        return texel_map.get_texel_map(self.object, self.uv_map, (width, height), tile)

    def batch(self):
        return texel_map.get_position_batch(self.object, self.uv_map)


class SyntheticSurface:
    """A texel map and a triangle soup given as arrays, for the self-test."""

    __slots__ = ('position', 'normal', 'triangles')

    def __init__(self, positions: np.ndarray, normals: np.ndarray, triangles: np.ndarray):
        width = positions.shape[1]
        self.position = raster._float_texture(positions, 'RGBA32F', width)
        self.normal = raster._float_texture(normals, 'RGBA16F', width)
        self.triangles = np.ascontiguousarray(triangles, dtype=np.float32).reshape(-1, 3)

    def texel_map(self, width: int, height: int, tile: int):
        return self

    def batch(self):
        return batch_for_shader(_shaders()["depth"], 'TRIS', {"position": self.triangles})


class ViewSpec:
    """The surface, region and matrices of a `VIEW` op at build time.

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

        Its view matrix is the stored object-to-view matrix times the
        inverse of the object's current world matrix.
        """
        obj = op.object
        stored = np.array(op.view_matrix, dtype=np.float64)
        world = np.array(obj.matrix_world, dtype=np.float64)
        return cls(ObjectSurface(obj, op.uv_map), op.region_size, stored @ np.linalg.inv(world),
                   np.array(op.projection_matrix, dtype=np.float64), op.through)


def view_block(spec: ViewSpec, half_width: float, reach: float) -> gpu.types.GPUUniformBuf:
    """The `PSViewBlock` uniform buffer for *spec* (176 bytes, std140)."""
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

    Only textures are kept. A framebuffer works only in the context that
    created it, so each build makes its own.
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
        with core.saved_state(), framebuffer.bind():
            framebuffer.clear(color=(3.0e38, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.depth_test_set('LESS')
            gpu.state.depth_mask_set(True)
            shader.bind()
            shader.uniform_block("view_block", block)
            batch.draw(shader)
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

_view_self_test_result: bool | None = None


def view_self_test() -> bool | None:
    """True when this GPU draws `VIEW` ops correctly. Runs once per session, on the first `VIEW` build.

    Renders `raster_selftest.self_test_chain` for the orthographic and the
    perspective scene, each with Through off and on. Compares them with
    `SELF_TEST_VIEW_EXPECTED` within `SELF_TEST_TOLERANCE`. A failure
    blocks only `VIEW` ops, with `SELF_TEST`. That is because the view
    passes use a uniform buffer, a depth target and a depth pass that
    selections drawn in UV space never touch. The `raster_selftest`
    docstring lists what it cannot catch. None and exceptions are handled
    as in `raster.self_test`.
    """
    global _view_self_test_result
    if _view_self_test_result is None:
        # Imported here for the reason `raster.self_test` gives.
        from . import raster_selftest
        _view_self_test_result = raster_selftest.run_view_test()
    return _view_self_test_result


def release() -> None:
    """Free the shaders and region targets and forget the self-test result. Called by `raster.release`."""
    global _view_self_test_result
    _gpu.clear()
    _targets.clear()
    _view_self_test_result = None
