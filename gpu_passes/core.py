"""Helpers shared by every GPU pass.

Three things differ between the Blender versions the addon supports.
They are handled here, so each pass does not have to:

- Background Blender starts with no GPU context. 4.2 to 5.1 have no way
  to get one, so passes are unavailable there and callers get None
  instead of an exception. 5.2 added `gpu.init()`, which makes a context
  from EGL without a display. It is documented to raise `SystemError`
  on failure. But on 5.2.1 and 5.3 alpha, when EGL has no usable
  driver, it crashes Blender with a segfault instead. So
  `gpu_available()`, which calls it, is only called on a path that is
  about to draw. `gpu_known()` answers without starting a context. A
  windowed session always has a context.
- On 4.2, `GPUFrameBuffer.read_color` reports reversed strides for a
  multi-dimensional `Buffer`. So reads go through a one-dimensional
  buffer and numpy does the reshape (PS-096 spike 4).
- On 4.2, `GPUFrameBuffer.viewport_set` takes no arguments at all, not
  even keywords. It is never called, because binding a framebuffer
  already sets the viewport to its size on both 4.2 and 5.x.
"""
import contextlib
import logging

import bpy
import gpu
import numpy as np

log = logging.getLogger(__name__)

# Two counter-clockwise triangles over the unit square, the geometry of
# every pass that covers its whole target.
UNIT_QUAD = {"position": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
                          (0.0, 0.0), (1.0, 1.0), (0.0, 1.0))}

BAND_ROWS = 512
"""Rows `draw_in_bands` draws before it makes the GPU finish."""

# The vertex shader of a pass drawn with `draw_in_bands`. The shader must
# declare `target_size` and `rows` push constants and a smooth VEC2
# `v_texel`, which gives the fragment its position in target pixels.
BAND_VERTEX_SOURCE = """
void main()
{
  /* One quad per band: x spans the target, y spans rows.x to rows.y. */
  v_texel = vec2(position.x * target_size.x, mix(rows.x, rows.y, position.y));
  gl_Position = vec4(v_texel / target_size * 2.0 - 1.0, 0.0, 1.0);
}
"""

_available: bool | None = None
_unused_texture = None


def unused_sampler() -> gpu.types.GPUTexture:
    """A 1x1 zero texture to bind to a sampler that the pass does not read.

    Every sampler declared in the shader create-info must be bound, even
    if the shader never reads it. An unbound sampler is an error on
    Vulkan. A GLSL sampler does not care about the texture format behind
    it, so one `R32F` texture serves every pass.
    """
    global _unused_texture
    if _unused_texture is None:
        _unused_texture = gpu.types.GPUTexture(
            (1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [0.0]))
    return _unused_texture


def release_unused_sampler() -> None:
    """Drop the texture `unused_sampler` keeps. The next call makes a new one.

    Every module that binds it calls this from its own `release`. So a
    caller that releases only the modules it used still frees it before
    the GPU context goes away.
    """
    global _unused_texture
    _unused_texture = None


@contextlib.contextmanager
def saved_state():
    """Put blending, depth test and depth write back as they were on entry."""
    saved = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
    try:
        yield
    finally:
        gpu.state.blend_set(saved[0])
        gpu.state.depth_test_set(saved[1])
        gpu.state.depth_mask_set(saved[2])


@contextlib.contextmanager
def offscreen_state(blend: str = 'NONE'):
    """Set up `gpu.state` for a pass into an offscreen target, then restore it.

    Blending is set to *blend*. Depth test and depth write are off, so
    each fragment lands as the shader wrote it. Face culling is turned
    off, because a mirrored UV island has reversed winding. Colour writes
    are turned on. `gpu.state` cannot read back those last two settings,
    so they are not restored. They stay culling off and all colour writes
    on, which is Blender's default.
    """
    with saved_state():
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        gpu.state.face_culling_set('NONE')
        gpu.state.color_mask_set(True, True, True, True)
        yield


def draw_in_bands(framebuffer: gpu.types.GPUFrameBuffer, height: int, draw_band) -> None:
    """Bind *framebuffer* and call ``draw_band(first, last)`` for each band of its rows.

    A band is `BAND_ROWS` rows. It is read at call time, so the view
    self-test (`selection.view_raster.self_test_chain`) can change it.
    After every band but the last, one texel is read back. That makes the
    GPU finish the band before the next one is queued, which limits the
    GPU time of any single command. A slow software rasteriser or a heavy
    pass is then far less likely to trip a driver watchdog (i915 preempts
    after 640 ms, Windows after 2 s).
    """
    rows = BAND_ROWS
    # Four floats for any target. Vulkan writes every component of the
    # target's format (up to four), and OpenGL writes the four asked for.
    sync = gpu.types.Buffer('FLOAT', 4)
    with framebuffer.bind():
        for first in range(0, height, rows):
            last = min(height, first + rows)
            draw_band(first, last)
            if last < height:
                framebuffer.read_color(0, first, 1, 1, 4, 0, 'FLOAT', data=sync)


def gpu_available() -> bool:
    """True when the `gpu` module can draw in this session.

    The answer cannot change while Blender runs, so it is probed once.
    """
    global _available
    if _available is not None:
        return _available
    if not bpy.app.background:
        _available = True
    elif not hasattr(gpu, 'init'):
        log.info("Background Blender has no GPU context before 5.2; "
                 "GPU passes are unavailable")
        _available = False
    else:
        try:
            gpu.init()
        except SystemError as error:
            log.warning("Could not start a background GPU context: %s", error)
            _available = False
        else:
            _available = True
    return _available


def gpu_known() -> bool | None:
    """Like `gpu_available()`, but never starts a context.

    Returns None in a background session of 5.2 or later that has not
    called `gpu_available()` yet, because there the answer needs
    `gpu.init()`.
    """
    if _available is not None:
        return _available
    if not bpy.app.background:
        return True
    if not hasattr(gpu, 'init'):
        return False
    return None


def _read(framebuffer, width: int, first: int, count: int, slot: int, channels: int,
          kind: str, dtype) -> np.ndarray:
    """Rows *first* to *first* + *count* of a colour slot, as a `(count, width, channels)` array.

    The result is a copy, because a `np.frombuffer` view would still point
    at the `Buffer`, which is freed when this returns.
    """
    buffer = gpu.types.Buffer(kind, width * count * channels)
    with framebuffer.bind():
        framebuffer.read_color(0, first, width, count, channels, slot, kind, data=buffer)
    return np.frombuffer(buffer, dtype=dtype).reshape(count, width, channels).copy()


def read_color(framebuffer: gpu.types.GPUFrameBuffer, width: int, height: int,
               slot: int = 0, *, channels: int = 4) -> np.ndarray:
    """A float colour slot of *framebuffer* as a `(height, width, channels)` array.

    Row 0 is the bottom of the image, matching `Image.pixels`.

    *channels* must be the number of components the slot's texture holds:
    4 for an `RGBA16F` or `RGBA32F` target, 1 for `R32F`. Vulkan writes
    every component of the texture's format, whatever count it is given,
    so a smaller count overruns the buffer.
    """
    return _read(framebuffer, width, 0, height, slot, channels, 'FLOAT', np.float32)


def read_color_bytes(framebuffer: gpu.types.GPUFrameBuffer, width: int, first: int, last: int,
                     *, channels: int = 4) -> np.ndarray:
    """Rows *first* to *last* of a byte colour slot, as a uint8 `(rows, width, channels)` array.

    Only for an `RGBA8` or `R8` texture. *channels* is its component
    count, as for `read_color`. Reading a float texture as bytes returns
    zeros on Vulkan, so a caller that wants bytes draws into a byte
    target first and lets the GPU do the rounding (PS-091).

    It reads a band of rows, not the whole slot. One read of 4096 rows
    stalls for about a second, which is too long for a modal operator to
    wait between events.
    """
    return _read(framebuffer, width, first, last - first, 0, channels, 'UBYTE', np.uint8)


def tile_offset(tile: int) -> tuple[float, float]:
    """The UV-space origin of a UDIM tile number (1001 is the 0..1 square)."""
    index = tile - 1001
    return float(index % 10), float(index // 10)
