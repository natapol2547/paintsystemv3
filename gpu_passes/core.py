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
import logging

import bpy
import gpu
import numpy as np

log = logging.getLogger(__name__)

_available: bool | None = None


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


def read_color(framebuffer: gpu.types.GPUFrameBuffer, width: int, height: int,
               slot: int = 0, *, rows: tuple[int, int] | None = None) -> np.ndarray:
    """A float colour slot of *framebuffer* as a `(rows, width, 4)` array.

    Row 0 is the bottom of the image, matching `Image.pixels`. The result
    is a copy: a `np.frombuffer` view stays backed by the `Buffer`, which
    is freed when this returns.

    *rows* is a half-open ``(first, last)`` range, defaulting to the whole
    framebuffer. One read of 4096 rows stalls for about a second, which
    is too long to hold a modal operator between events, so a caller that
    has to stay responsive asks for a band at a time.
    """
    first, last = (0, height) if rows is None else rows
    count = last - first
    buffer = gpu.types.Buffer('FLOAT', width * count * 4)
    with framebuffer.bind():
        framebuffer.read_color(0, first, width, count, 4, slot, 'FLOAT', data=buffer)
    return np.frombuffer(buffer, dtype=np.float32).reshape(count, width, 4).copy()


def read_color_bytes(framebuffer: gpu.types.GPUFrameBuffer, width: int, height: int,
                     slot: int = 0, *, rows: tuple[int, int] | None = None) -> np.ndarray:
    """`read_color` for a slot that holds bytes, as a uint8 `(rows, width, 4)` array.

    Only for an `RGBA8` texture. Reading a float texture as bytes returns
    zeros on Vulkan, so a caller wanting bytes draws into a byte target
    first and lets the GPU round (PS-091).
    """
    first, last = (0, height) if rows is None else rows
    count = last - first
    buffer = gpu.types.Buffer('UBYTE', width * count * 4)
    with framebuffer.bind():
        framebuffer.read_color(0, first, width, count, 4, slot, 'UBYTE', data=buffer)
    return np.frombuffer(buffer, dtype=np.uint8).reshape(count, width, 4).copy()


def tile_offset(tile: int) -> tuple[float, float]:
    """The UV-space origin of a UDIM tile number (1001 is the 0..1 square)."""
    index = tile - 1001
    return float(index % 10), float(index // 10)
