"""Plumbing shared by every GPU pass.

Three things differ between the Blender versions the addon supports, and
are dealt with here rather than in each pass:

- Background Blender starts with no GPU context. 4.2 to 5.1 have no way
  to get one, so passes are unavailable there and callers get None
  rather than an exception. 5.2 added `gpu.init()`, which builds one from
  EGL and needs no display. It is documented to raise `SystemError` when
  it fails, but on 5.2.1 and 5.3 alpha it terminates Blender with a
  segmentation fault instead when EGL has no usable driver. So
  `gpu_available()`, which calls it, is only called from a path that is
  about to draw; `gpu_known()` answers without starting a context. A
  windowed session always has a context.
- `GPUFrameBuffer.read_color` reports reversed strides for a
  multi-dimensional `Buffer` on 4.2, so reads go through a
  one-dimensional buffer and numpy does the reshape (PS-096 spike 4).
- `GPUFrameBuffer.viewport_set` takes no arguments at all on 4.2, not
  even keywords. It is never called: binding a framebuffer already sets
  the viewport to that framebuffer's size on both versions.
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

_available: bool | None = None
_unused_texture = None


def unused_sampler() -> gpu.types.GPUTexture:
    """A 1x1 zero texture to bind to a sampler the pass does not read.

    A sampler a create-info declares has to be bound even where the
    shader never reads it: an unbound one is an error on Vulkan. A GLSL
    sampler does not care what format is behind it, so one `R32F`
    texture serves every pass.
    """
    global _unused_texture
    if _unused_texture is None:
        _unused_texture = gpu.types.GPUTexture(
            (1, 1), format='R32F', data=gpu.types.Buffer('FLOAT', 1, [0.0]))
    return _unused_texture


def release_unused_sampler() -> None:
    """Drop the texture `unused_sampler` keeps; the next call makes it again.

    Every module that binds it calls this from its own `release`, so a
    caller that releases only the modules it used still frees it before
    the GPU context goes.
    """
    global _unused_texture
    _unused_texture = None


@contextlib.contextmanager
def offscreen_state(blend: str = 'NONE'):
    """Set `gpu.state` up for a pass into an offscreen target, and restore it after.

    Blending is *blend*, and depth test and depth write are off, so each
    fragment lands as the shader wrote it. Face culling is turned off,
    because a mirrored UV island reverses its winding, and colour writes
    are turned on. `gpu.state` cannot read either of those two, so they
    are left that way afterwards, which is Blender's default.
    """
    saved = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
    gpu.state.blend_set(blend)
    gpu.state.depth_test_set('NONE')
    gpu.state.depth_mask_set(False)
    gpu.state.face_culling_set('NONE')
    gpu.state.color_mask_set(True, True, True, True)
    try:
        yield
    finally:
        gpu.state.blend_set(saved[0])
        gpu.state.depth_test_set(saved[1])
        gpu.state.depth_mask_set(saved[2])


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
    """`gpu_available()` without starting a context.

    None in a background session of 5.2 or later that has not called
    `gpu_available()` yet, where the answer needs `gpu.init()`.
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

    The result is a copy: a `np.frombuffer` view stays backed by the
    `Buffer`, which is freed when this returns.
    """
    buffer = gpu.types.Buffer(kind, width * count * channels)
    with framebuffer.bind():
        framebuffer.read_color(0, first, width, count, channels, slot, kind, data=buffer)
    return np.frombuffer(buffer, dtype=dtype).reshape(count, width, channels).copy()


def read_color(framebuffer: gpu.types.GPUFrameBuffer, width: int, height: int,
               slot: int = 0, *, channels: int = 4) -> np.ndarray:
    """A float colour slot of *framebuffer* as a `(height, width, channels)` array.

    Row 0 is the bottom of the image, matching `Image.pixels`.

    *channels* has to be the number of components the slot's texture
    holds: 4 for an `RGBA16F` or `RGBA32F` target, 1 for `R32F`. Vulkan
    writes every component of the texture's format whatever count it is
    given, so a smaller count overruns the buffer.
    """
    return _read(framebuffer, width, 0, height, slot, channels, 'FLOAT', np.float32)


def read_color_bytes(framebuffer: gpu.types.GPUFrameBuffer, width: int, first: int, last: int,
                     *, channels: int = 4) -> np.ndarray:
    """Rows *first* to *last* of a byte colour slot, as a uint8 `(rows, width, channels)` array.

    Only for an `RGBA8` or `R8` texture, with *channels* its component
    count as for `read_color`. Reading a float texture as bytes returns
    zeros on Vulkan, so a caller wanting bytes draws into a byte target
    first and lets the GPU round (PS-091).

    It reads a band rather than the whole slot: one read of 4096 rows
    stalls for about a second, which is too long to hold a modal
    operator between events.
    """
    return _read(framebuffer, width, first, last - first, 0, channels, 'UBYTE', np.uint8)


def tile_offset(tile: int) -> tuple[float, float]:
    """The UV-space origin of a UDIM tile number (1001 is the 0..1 square)."""
    index = tile - 1001
    return float(index % 10), float(index // 10)
