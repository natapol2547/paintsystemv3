"""Selection masks, built on the GPU from the ops on the tree (PS-091).

A mask is one `R32F` texture the size of the layer image (or of one UDIM
tile), with coverage from 0 to 1 per texel. `get_mask` builds it from
`PaintSystemSelection.ops`, and `peek_mask` returns a cached one for draw
callbacks. Nothing stores masks, so undo, redo and a reload need no mask
history.

Building a mask:

- Each op is one full-screen pass. The fragment shader computes the op's
  coverage `c` at the texel centre and combines it with the previous
  mask `m`, read with `texelFetch`. So the passes ping-pong between two
  targets and never blend. `REPLACE` writes `c`, `ADD` writes
  `max(m, c)`, `SUBTRACT` writes `min(m, 1 - c)` and `INTERSECT` writes
  `min(m, c)`. `INVERT` writes `1 - m` whatever its mode says. A first
  op with a mode other than `REPLACE` combines with an empty mask.
- Coverage comes from a signed distance `s` in texels, positive inside.
  With `half_width = 0.5 * max(feather, 1 if antialias else 0)`, a hard
  edge is `s > 0` and a soft edge is the smoothstep of
  `(s + half_width) / (2 * half_width)`. So a feathered edge rises from
  0 to 1 over the feather width, centred on the outline.
- On a hard edge (no feather, anti-alias off), a box or ellipse leaves
  out texel centres exactly on its outline. A lasso uses the half-open
  even-odd rule, which counts a centre on a right or bottom edge but not
  one on a left or top edge. So the same rectangle drawn as a box and as
  a lasso can differ by one column and one row when its edges pass
  through texel centres.
- Box and ellipse distances are analytic. The ellipse uses Eberly's
  robust distance, bisecting the root in `u = s + 1`, which float32
  resolves to about 3e-7 of the longer radius. Texels far from the
  outline skip it. Shape parameters are uploaded split into whole and
  fractional texels. A plain float32 texel coordinate is off by up to
  2.4e-4 texels at 4K, which moves feathered coverage by more than 1e-5.
- Lasso fill and distance come from the tables `outline.py` builds.
- An outlined `VIEW` op (drawn in the 3D view) goes to
  `view_raster.run_view_pass`, which draws it through its object's texel
  map. Its outline uses the same shape and lasso shaders, compiled with
  `SIGNED_DISTANCE` so they write the clamped signed distance instead.
- A build draws in bands (`gpu_passes.core.draw_in_bands`) and reads one
  texel back between bands. That limits the GPU time of each command, so
  a slow software rasteriser or a dense lasso with a wide feather is far
  less likely to trip a driver watchdog.
- The passes set blend, depth test and depth write and restore them.
  They also turn face culling off and colour writes on and leave them
  that way, because `gpu.state` cannot read either.

Caching:

- The cache key is the digest of the op chain, the target size and the
  UDIM tile (`PaintSystemSelection.prefix_digests`). So an undo, a redo
  or a second tree with the same ops finds the mask already built. A
  `VIEW` op's part of the digest includes the content key of its
  object's surface, which `view_key` provides.
- A build caches the final mask and the one before it. That makes
  removing the last op, or undoing an appended op, free.
- Before each build, the least recently used masks are evicted until the
  build's targets fit `CACHE_BUDGET`. The cached prefix the build resumes
  from is never evicted, so the cache can end up one mask over budget.
- The next `get_mask` may evict any mask, including the one the previous
  call returned. An evicted mask's `texture` raises `ReferenceError`. So
  do not keep masks across calls, redraws or timer ticks. Call
  `get_mask` or `peek_mask` again.
"""
import logging

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ..gpu_passes import core, surface
from ..lru import LRUCache
from ..props.selection import FEATHER_MAX, OUTLINELESS_KINDS, POINTS_KEY, SPACELESS_KINDS, points_view
from . import outline, view_raster
from .raster_glsl import (LASSO_FRAGMENT_SOURCE, QUANTISE_FRAGMENT_SOURCE, QUANTISE_VERTEX_SOURCE,
                          SHAPE_FRAGMENT_SOURCE)

log = logging.getLogger(__name__)

CACHE_BUDGET = 512 << 20
"""Video memory the cached masks may hold, in bytes. A 4K mask is 64 MB."""

POOL_LIMIT = 2
"""Evicted textures kept for reuse by the next build of the same size."""

MAX_TEXELS = 1 << 26
"""Largest mask, in texels: 8192 x 8192, or 256 MB per mask."""

MIN_RADIUS = 1e-3
"""Ellipse radius in texels below which the ellipse covers nothing."""

SUPPORTED_KINDS = frozenset(('BOX', 'ELLIPSE', 'LASSO', 'ALL', 'INVERT'))
"""Op kinds this module can rasterise. `FACES`, `RASTER` and `TRANSFORM`
will be added with the tools that create them (PS-093, PS-094)."""

_MODE_UNIFORMS = {'REPLACE': 0, 'ADD': 1, 'SUBTRACT': 2, 'INTERSECT': 3}
_SHAPE_ALL, _SHAPE_BOX, _SHAPE_ELLIPSE, _SHAPE_INVERT, _SHAPE_NOTHING = range(5)

_UNSUPPORTED_KIND_MESSAGES = {
    'FACES': "Selections with a faces operation cannot be built yet",
    'RASTER': "Selections with a raster operation cannot be built yet",
    'TRANSFORM': "Selections with a transform operation cannot be built yet",
}
MESSAGES = {
    'NO_GPU': "This Blender session has no GPU context, so the selection cannot be built",
    'NO_SIZE': "The selection has no image with pixels to take its size from",
    'TOO_LARGE': "The image is too large for a selection mask",
    'VIEW': "This selection's view is invalid",
    'SURFACE': "The object or UV map this selection was drawn on is gone",
    'EDIT_MODE': "Leave Edit Mode to use this selection",
    'POINTS': "A selection operation has malformed points",
    'TOO_COMPLEX': "The lasso outline is too complex to build",
    'SELF_TEST': "The GPU failed the selection self-test, so selections are disabled in this session",
    'GPU_ERROR': "The GPU could not build the selection right now; try again",
}

GEOMETRY_REASONS = frozenset(('SURFACE', 'VIEW', 'EDIT_MODE'))
"""Failure reasons that depend on the objects a selection was drawn on,
not on its ops. Remembering such a failure by digest would outlive its
cause."""

_gpu: dict = {}


def _mask_shader(name: str, fragment: str, lasso: bool) -> gpu.types.GPUShader:
    """Build a mask pass shader. Its push constants stay under Vulkan's 128 bytes."""
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
    info.vertex_source(core.BAND_VERTEX_SOURCE)
    info.fragment_source(fragment)
    return gpu.shader.create_from_info(info)


def _quantise_shader() -> gpu.types.GPUShader:
    """Rounds a mask to 8 bits on the GPU, for `SelectionMask.read_bytes`."""
    info = gpu.types.GPUShaderCreateInfo()
    info.sampler(0, 'FLOAT_2D', "mask")
    info.vertex_in(0, 'VEC2', "position")
    info.fragment_out(0, 'FLOAT', "out_value")
    info.vertex_source(QUANTISE_VERTEX_SOURCE)
    info.fragment_source(QUANTISE_FRAGMENT_SOURCE)
    return gpu.shader.create_from_info(info)


def _resources() -> dict:
    """Shaders and their batches, built once per session."""
    if not _gpu:
        # The distance variants write the signed distance clamped to
        # +-half_width instead of combining coverage with the previous mask.
        coverage = "const bool SIGNED_DISTANCE = false;\n"
        distance = "const bool SIGNED_DISTANCE = true;\n"
        shape = _mask_shader("shape", coverage + SHAPE_FRAGMENT_SOURCE, False)
        lasso = _mask_shader("lasso", coverage + LASSO_FRAGMENT_SOURCE, True)
        shape_distance = _mask_shader("shape_distance", distance + SHAPE_FRAGMENT_SOURCE, False)
        lasso_distance = _mask_shader("lasso_distance", distance + LASSO_FRAGMENT_SOURCE, True)
        quantise = _quantise_shader()
        _gpu.update(
            shape=(shape, batch_for_shader(shape, 'TRIS', core.UNIT_QUAD)),
            lasso=(lasso, batch_for_shader(lasso, 'TRIS', core.UNIT_QUAD)),
            shape_distance=(shape_distance, batch_for_shader(shape_distance, 'TRIS', core.UNIT_QUAD)),
            lasso_distance=(lasso_distance, batch_for_shader(lasso_distance, 'TRIS', core.UNIT_QUAD)),
            quantise=(quantise, batch_for_shader(quantise, 'TRIS', core.UNIT_QUAD)),
        )
    return _gpu


class MaskUnavailable(RuntimeError):
    """Raised when a mask cannot be built. `str()` is a message for the UI.

    `reason` is one of `NO_GPU`, `NO_SIZE`, `TOO_LARGE`, `UNSUPPORTED`,
    `TOO_COMPLEX`, `SELF_TEST`, `GPU_ERROR` or one of `GEOMETRY_REASONS`.

    - `UNSUPPORTED`: an op kind this module cannot build yet, or an op
      with malformed points.
    - `SURFACE`: a `VIEW` op's object or UV map is gone.
    - `VIEW`: a `VIEW` op's view cannot be used.
    - `EDIT_MODE`: a `VIEW` op's object is in Edit Mode.
    - `GPU_ERROR`: the GPU raised while building, as it does with no
      active context. A later call may succeed.

    `op_index` is the index in `selection.ops` of the op at fault, or -1
    when no single op is at fault.
    """

    def __init__(self, reason: str, message: str, op_index: int = -1):
        super().__init__(message)
        self.reason = reason
        self.op_index = op_index


class SelectionMask:
    """One built mask, an `R32F` texture with row 0 at the bottom like `Image.pixels`.

    The cache owns the texture. Once the mask is evicted, `alive` is False
    and `texture`, `read` and `read_bytes` raise `ReferenceError`.
    """

    __slots__ = ('_texture', 'width', 'height', 'key', 'alive', '_empty')

    def __init__(self, texture, width: int, height: int, key: bytes):
        self._texture = texture
        self.width = width
        self.height = height
        self.key = key
        self.alive = True
        self._empty = None

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
        values = core.read_color(framebuffer, self.width, self.height, channels=1)
        return values.reshape(self.height, self.width)

    def read_bytes(self) -> np.ndarray:
        """The mask as uint8 `(height, width)`, each value `floor(v * 255 + 0.5)`.

        Quantised on the GPU into an `R8` target, so the read back is a
        quarter the size of `read`'s. Reading an `R32F` texture as bytes
        directly returns zeros on Vulkan. The rounding runs in float32, so
        a value within float32 error of a half step can round the other
        way than the same formula in float64 would.
        """
        shader, batch = _resources()["quantise"]
        target = gpu.types.GPUTexture((self.width, self.height), format='R8')
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
        with core.offscreen_state():
            with framebuffer.bind():
                shader.uniform_sampler("mask", self.texture)
                batch.draw(shader)
        values = core.read_color_bytes(framebuffer, self.width, 0, self.height, channels=1)
        values = values.reshape(self.height, self.width)
        self._empty = not values.any()
        return values

    def is_empty(self) -> bool:
        """True when no texel reaches 0.5 / 255, so nothing survives as 8 bits.

        Such a mask selects nothing a stroke could show, so it counts as no
        selection. The answer is read back once per mask and remembered.
        `read_bytes` also stores it.
        """
        if self._empty is None:
            self.read_bytes()
        return self._empty


class OpSpec:
    """The parts of an op a pass reads, in UV coordinates.

    Rendering does not need a `PaintSystemSelectionOp`, so tests and the
    self-test describe ops with this class instead.
    """

    __slots__ = ('kind', 'mode', 'feather', 'antialias', 'points', 'view')

    def __init__(self, kind: str, mode: str = 'REPLACE', feather: float = 0.0,
                 antialias: bool = True, points=(), view=None):
        self.kind = kind
        self.mode = mode
        self.feather = float(feather)
        self.antialias = bool(antialias)
        self.points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        # A `view_raster.ViewSpec` for an outlined `VIEW` op, whose points
        # are in region pixels. None for ops drawn in UV space.
        self.view = view

    @classmethod
    def from_op(cls, op) -> "OpSpec":
        """The spec of a `PaintSystemSelectionOp`, reading its points without a copy per value.

        Malformed points read as no points. `get_mask` reports them before
        it builds.
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
        drawn_in_view = op.space == 'VIEW' and op.kind not in SPACELESS_KINDS
        return cls(op.kind, op.mode, op.feather, op.antialias, points,
                   view_raster.ViewSpec.from_op(op) if drawn_in_view else None)


def _half_width(feather: float, antialias: bool) -> float:
    """Half the soft edge width in texels, with the feather clamped to `FEATHER_MAX`."""
    feather = min(max(feather, 0.0), FEATHER_MAX) if feather == feather else 0.0
    return 0.5 * max(feather, 1.0 if antialias else 0.0)


def _float_texture(values: np.ndarray, fmt: str, width: int) -> gpu.types.GPUTexture:
    """A float texture *width* texels wide holding *values* row by row."""
    flat = np.ascontiguousarray(values, dtype=np.float32).ravel()
    channels = {'R32F': 1, 'RG32F': 2, 'RGBA32F': 4, 'RGBA16F': 4}[fmt]
    height = len(flat) // (width * channels)
    return gpu.types.GPUTexture((width, height), format=fmt, data=gpu.types.Buffer('FLOAT', len(flat), flat))


def _run_pass(spec: OpSpec, source, target, width: int, height: int, tile: int) -> None:
    """Draw one op into *target*, combining with *source* (None for an empty mask).

    An op with a view goes to `view_raster.run_view_pass`. Raises
    `outline.OutlineTooComplex` for a lasso past the table limits.
    """
    if spec.view is not None:
        view_raster.run_view_pass(spec, source, target, width, height, tile)
        return
    ints = {"mode": _MODE_UNIFORMS.get(spec.mode, 0), "use_previous": 0 if source is None else 1}
    texels = (spec.points - core.tile_offset(tile)) * (width, height)
    _draw_outline(spec, texels, _half_width(spec.feather, spec.antialias), ints, source, target, width, height,
                  distance=False)


def _run_distance_pass(spec: OpSpec, target, width: int, height: int, reach: float) -> None:
    """Draw the signed distance to *spec*'s outline into *target*, clamped to +-*reach*.

    The points are already in *target*'s pixels. The distance is positive
    inside, like `s` in the coverage passes. This is stage A of
    `view_raster.run_view_pass`. Raises `outline.OutlineTooComplex` for a
    lasso past the table limits.
    """
    # The distance variants do not read mode or use_previous. The GL
    # backend strips both from the shader, so setting them would be an
    # error.
    _draw_outline(spec, spec.points, float(reach), {}, None, target, width, height, distance=True)


def _draw_outline(spec: OpSpec, texels: np.ndarray, half_width: float, ints: dict, source, target,
                  width: int, height: int, distance: bool) -> None:
    """The draw shared by `_run_pass` and `_run_distance_pass`. *texels* are in target pixels."""
    res = _resources()
    # Bound to every sampler the pass does not read, because an unbound
    # sampler is an error on Vulkan.
    placeholder = core.unused_sampler()
    suffix = "_distance" if distance else ""
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
        shader, batch = res["lasso" + suffix]
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
                # A box edge further outside the target than the soft edge
                # changes nothing, so clamp it to keep the values small.
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
        shader, batch = res["shape" + suffix]

    def draw_band(first, last):
        for name, value in ints.items():
            shader.uniform_int(name, value)
        for name, value in floats.items():
            shader.uniform_float(name, value)
        for name, texture in samplers.items():
            shader.uniform_sampler(name, texture)
        shader.uniform_float("rows", (float(first), float(last)))
        batch.draw(shader)

    core.draw_in_bands(gpu.types.GPUFrameBuffer(color_slots=(target,)), height, draw_band)


def _run_chain(specs, source, targets, width: int, height: int, tile: int) -> None:
    """Run *specs* in order from *source*, the last into ``targets[0]``.

    With two or more specs, the one before the last lands in
    ``targets[1]``. A lasso past the table limits raises `MaskUnavailable`
    with reason `TOO_COMPLEX` and its index into *specs*. A `VIEW` op
    whose surface cannot be drawn raises `SURFACE` with its index.
    """
    passes = len(specs)
    failure = None
    with core.offscreen_state():
        for step, spec in enumerate(specs):
            target = targets[(passes - 1 - step) % 2] if passes >= 2 else targets[0]
            try:
                _run_pass(spec, source, target, width, height, tile)
            except outline.OutlineTooComplex:
                failure = ('TOO_COMPLEX', MESSAGES['TOO_COMPLEX'], step)
                break
            except MaskUnavailable as error:
                failure = (error.reason, str(error), step)
                break
            source = target
    if failure is not None:
        # Raise outside the except block, with the textures dropped. A
        # traceback keeps every frame's locals alive, so a held exception
        # would otherwise keep the textures alive past `release()`.
        source = target = targets = None
        raise MaskUnavailable(*failure)


# ── Self-test ────────────────────────────────────────────────────────

_self_test_result: bool | None = None


def self_test() -> bool | None:
    """True when this GPU draws masks correctly. Runs once per session, on the first build.

    Renders `raster_selftest.SELF_TEST_OPS` and `SELF_TEST_WIDE_OPS` and
    passes only when both match their expected texels within
    `SELF_TEST_TOLERANCE`. If a driver gets a checked path wrong, the test
    fails and every later build raises `SELF_TEST` instead of giving tools
    a wrong mask. Neither chain checks a hard ellipse, distance cells wider
    than 32 texels, distance tables past one data texture row, or a tile
    other than 1001.

    Returns None when the test could not run because the GPU raised
    `RuntimeError`, as it does with no active context. None is not
    remembered, so the next build runs the test again. Any other exception
    counts as a failure.
    """
    global _self_test_result
    if _self_test_result is None:
        # Imported here, not at the top: `raster_selftest` builds its ops
        # with `OpSpec` as it loads, so this module must finish loading first.
        from . import raster_selftest
        _self_test_result = raster_selftest.run_mask_test()
    return _self_test_result


def render(specs, width: int, height: int, tile: int = 1001) -> np.ndarray:
    """Run *specs* from an empty mask and read the result, float32 `(height, width)`.

    Not cached and not gated by the self-test. Used by the self-test
    itself and by tests. Raises `MaskUnavailable` for `NO_GPU`, `NO_SIZE`,
    `TOO_LARGE`, `UNSUPPORTED` (index into *specs*) and `TOO_COMPLEX`.
    """
    problem = _target_problem(width, height)
    if problem is not None:
        raise MaskUnavailable(problem, MESSAGES[problem])
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
        return SelectionMask(targets[0], width, height, b"").read()
    finally:
        # A raised error must not keep the targets alive (see `_run_chain`).
        targets = None


# ── Cache ────────────────────────────────────────────────────────────

_masks: LRUCache[bytes, SelectionMask] = LRUCache()
_pool: list[tuple[tuple[int, int], gpu.types.GPUTexture]] = []
_warned: set = set()
# Counters that tests read through `stats` to check how much GPU work a
# call did. The add-on itself never reads them.
_stats = dict(builds=0, passes=0, hits=0, allocations=0, evictions=0)


def image_size(image, tile: int = 1001) -> tuple[int, int]:
    """The size a mask for *image* is built at.

    That is the image's size, or for a tiled image the size of tile
    *tile*. It is (0, 0) when the image has no such tile, the tile has no
    pixels, or the image file is missing.
    """
    if image.source == 'TILED':
        for image_tile in image.tiles:
            if image_tile.number == tile:
                return int(image_tile.size[0]), int(image_tile.size[1])
        return 0, 0
    return int(image.size[0]), int(image.size[1])


def mask_size(size: tuple[int, int]) -> tuple[int, int]:
    """*size* as a pair of ints, which is what the digest and the textures take."""
    return int(size[0]), int(size[1])


def _target_problem(width: int, height: int, probe: bool = True) -> str | None:
    """The reason no mask can be built at this size, or None.

    With *probe* False, a background GPU context that has not started yet
    is not started just to answer. The size is then only checked against
    `MAX_TEXELS`, because reading the maximum texture size needs a
    context.
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


def view_key(op, peek: bool = False) -> bytes | None:
    """The content key of the surface *op* was drawn on, as the `prefix_digests` provider.

    Timers and operators resolve the key (`surface.resolve_key`), so a
    mask they build never uses a stale surface. Draw callbacks pass
    *peek*. That returns the last resolved key and, when it may be stale,
    requests a resolve on the next timer tick. So a draw shows the
    previous mask for one frame at most. Returns None when the op has no
    object or UV map, the object is not a mesh, its mesh is in Edit Mode,
    or it has no such UV map.
    """
    obj = op.object
    if obj is None or not op.uv_map:
        return None
    if peek:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        key, fresh = surface.peek_key(obj, op.uv_map, depsgraph)
        if not fresh:
            surface.request(obj, op.uv_map, depsgraph)
        return key
    return surface.resolve_key(obj, op.uv_map)


def invertible(matrix) -> bool:
    """True when `np.linalg.inv` can invert the 4 x 4 *matrix* (finite, non-zero float64 determinant).

    The select tools call it too, so they refuse exactly the objects a
    `VIEW` op could not be drawn on.
    """
    determinant = np.linalg.det(np.array(matrix, dtype=np.float64))
    return bool(determinant != 0.0 and np.isfinite(determinant))


def _view_problem(op, surface_key) -> str | None:
    """Why the outlined `VIEW` op *op* cannot be drawn, as one of `GEOMETRY_REASONS`, or None."""
    obj = op.object
    if obj is None or obj.type != 'MESH':
        return 'SURFACE'
    # An object deleted in the viewport stays in `bpy.data`, because the
    # op's pointer is a user of it. It is gone from the view layer, though.
    # Compare by identity, since a linked object can share its name with a
    # local one.
    if obj not in bpy.context.view_layer.objects.values():
        return 'SURFACE'
    if op.uv_map not in obj.data.uv_layers:
        return 'SURFACE'
    if min(op.region_size) <= 0:
        return 'VIEW'
    # Building the view block inverts both the current world matrix and
    # the stored view. The stored view cannot be inverted if the object
    # was flat when the op was drawn.
    if not invertible(obj.matrix_world) or not invertible(op.view_matrix):
        return 'VIEW'
    if surface_key(op) is None:
        # The mesh may be in Edit Mode through a linked duplicate.
        return 'EDIT_MODE' if obj.data.is_editmode else 'SURFACE'
    return None


def _problem(selection, width: int, height: int, probe: bool = True,
             surface_key=view_key) -> tuple[str, str, int] | None:
    """Why *selection* cannot be built at this size, as (reason, message, op index), or None.

    *probe* is passed on to `_target_problem`. *surface_key* is the
    provider that decides whether a `VIEW` op's surface exists.
    """
    reason = _target_problem(width, height, probe)
    if reason is not None:
        return reason, MESSAGES[reason], -1
    ops = selection.ops
    drawn_in_view = False
    for index in range(selection.chain_start(), len(ops)):
        op = ops[index]
        if op.kind not in SUPPORTED_KINDS:
            return 'UNSUPPORTED', _UNSUPPORTED_KIND_MESSAGES[op.kind], index
        if op.kind not in OUTLINELESS_KINDS:
            raw = op.get(POINTS_KEY)
            if raw is not None and points_view(raw) is None:
                return 'UNSUPPORTED', MESSAGES['POINTS'], index
        if op.space == 'VIEW' and op.kind not in SPACELESS_KINDS:
            reason = _view_problem(op, surface_key)
            if reason is not None:
                return reason, MESSAGES[reason], index
            drawn_in_view = True
    if _self_test_result is False or (drawn_in_view and view_raster._view_self_test_result is False):
        return 'SELF_TEST', MESSAGES['SELF_TEST'], -1
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


def availability(selection, size: tuple[int, int], tile: int = 1001, surface_key=view_key) -> str:
    """An empty string when `get_mask` can build *selection*, else the reason as a UI message.

    An empty selection counts as available. This is cheap enough for a
    `poll` or a draw callback. It builds nothing and does not run the
    self-test, so a GPU that has not been tested yet reports available.
    It also does not start a background GPU context, so a background
    session of Blender 5.2 or later reports available before its context
    has started. A draw callback passes the peek provider,
    `functools.partial(view_key, peek=True)`.
    """
    if not len(selection.ops):
        return ""
    width, height = mask_size(size)
    problem = _problem(selection, width, height, probe=False, surface_key=surface_key)
    return problem[1] if problem is not None else ""


def _cached_bytes() -> int:
    return sum(mask.video_memory for mask in _masks.values())


def _evict(reserve: int, keep: set, width: int, height: int) -> None:
    """Evict the least recently used masks until *reserve* more bytes fit the budget.

    Masks in *keep* stay. Evicted textures of the size being built go to
    the pool for the build to reuse.
    """
    for mask in _masks.trim(CACHE_BUDGET - reserve, lambda mask: mask.video_memory, keep):
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


def peek_mask(selection, size: tuple[int, int], tile: int = 1001, surface_key=view_key) -> SelectionMask | None:
    """The cached mask of *selection*, or None. Never builds and never raises.

    Meant for draw callbacks, which must not run passes. They show what
    is cached, and a tool or timer calls `get_mask`. A draw callback
    passes the peek provider, `functools.partial(view_key, peek=True)`.

    Also returns None when a `VIEW` op's object or view cannot be used
    now (`GEOMETRY_REASONS`). An object removed from the view layer or
    scaled to zero keeps its surface key, so the digest alone would still
    find the mask built before.
    """
    if not len(selection.ops):
        return None
    width, height = mask_size(size)
    if width <= 0 or height <= 0:
        return None
    key = selection.prefix_digests(width, height, tile, surface_key=surface_key)[-1]
    if key not in _masks:
        return None
    ops = selection.ops
    for index in range(selection.chain_start(), len(ops)):
        op = ops[index]
        if op.space == 'VIEW' and op.kind not in SPACELESS_KINDS and _view_problem(op, surface_key) is not None:
            return None
    return _masks.touch(key)


def get_mask(selection, size: tuple[int, int], tile: int = 1001, surface_key=view_key) -> SelectionMask | None:
    """The mask of *selection*, built or taken from the cache.

    *size* is the target image's size (`image_size`). *tile* picks the
    UDIM tile whose UV square maps onto the mask. Returns None for an
    empty selection. Raises `MaskUnavailable` when the mask cannot be
    built. The first failure for a given selection state is logged as a
    warning. Ops before the last `REPLACE` of a kind in `REPLACING_KINDS`
    do not change the cache key and cost no pass. A build with a `VIEW`
    op runs `view_raster.view_self_test` first.

    Must be called with a GPU context, as from an operator, a timer or a
    draw callback. Do not keep the result, because the next call may
    evict it.
    """
    ops = selection.ops
    if not len(ops):
        return None
    width, height = mask_size(size)
    problem = _problem(selection, width, height, surface_key=surface_key)
    if problem is not None:
        _raise(problem, selection.prefix_digests(max(width, 0), max(height, 0), tile, surface_key=surface_key)[-1])
    digests = selection.prefix_digests(width, height, tile, surface_key=surface_key)
    last = len(ops) - 1
    if digests[last] in _masks:
        _stats["hits"] += 1
        return _masks.touch(digests[last])
    passed = self_test()
    if passed is None:
        _raise(('GPU_ERROR', MESSAGES['GPU_ERROR'], -1), digests[last])
    if not passed:
        _raise(('SELF_TEST', MESSAGES['SELF_TEST'], -1), digests[last])

    start = selection.chain_start()
    source_index = None
    for index in range(last - 1, start - 1, -1):
        if digests[index] in _masks:
            source_index = index
            break
    first = start if source_index is None else source_index + 1
    specs = [OpSpec.from_op(ops[index]) for index in range(first, last + 1)]
    if any(spec.view is not None for spec in specs):
        passed = view_raster.view_self_test()
        if passed is None:
            _raise(('GPU_ERROR', MESSAGES['GPU_ERROR'], -1), digests[last])
        if not passed:
            _raise(('SELF_TEST', MESSAGES['SELF_TEST'], -1), digests[last])
    passes = len(specs)
    keep = set() if source_index is None else {digests[source_index]}
    _evict(min(passes, 2) * width * height * 4, keep, width, height)
    source = None if source_index is None else _masks.touch(digests[source_index]).texture
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
        # allocation fails. A later build may succeed.
        log.debug("Selection mask build failed: %s", str(error))
        failure = ('GPU_ERROR', MESSAGES['GPU_ERROR'], -1)
    if failure is not None:
        _return_to_pool(targets, width, height)
        # Nothing the traceback keeps may hold a texture (see `_run_chain`).
        source = targets = None
        _raise(failure, digests[last])
    _stats["passes"] += passes
    if passes >= 2:
        _masks[digests[last - 1]] = SelectionMask(targets[1], width, height, digests[last - 1])
    result = SelectionMask(targets[0], width, height, digests[last])
    _masks[digests[last]] = result
    return result


def invalidate() -> None:
    """Drop every cached mask and pooled texture. Held masks become invalid."""
    for mask in _masks.values():
        mask.alive = False
        mask._texture = None
    _masks.clear()
    _pool.clear()
    _warned.clear()


def release() -> None:
    """Free every GPU object this module and `view_raster` hold, and forget both self-tests.

    Called on unregister, while the GPU context still exists. In a
    background session, Python's own teardown runs after the context is
    gone, and freeing a texture then segfaults Blender.
    """
    global _self_test_result
    invalidate()
    _gpu.clear()
    view_raster.release()
    core.release_unused_sampler()
    _self_test_result = None


def stats() -> dict:
    """The test counters since `reset_stats`, plus what the cache holds now. Only tests call this."""
    out = dict(_stats)
    out.update(cached=len(_masks), pooled=len(_pool),
               video_memory=_cached_bytes() + sum(w * h * 4 for (w, h), _ in _pool))
    return out


def reset_stats() -> None:
    """Zero the counters `stats` reports."""
    for key in _stats:
        _stats[key] = 0
