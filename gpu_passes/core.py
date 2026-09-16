"""Plumbing shared by every GPU pass.

Three things differ between the Blender versions the addon supports, and
are dealt with here rather than in each pass:

- Background Blender starts with no GPU context. 5.0 added `gpu.init()`,
  which builds one from EGL and needs no display; 4.2 has no equivalent,
  so passes are unavailable there and callers get None rather than an
  exception. A windowed session always has a context.
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
        log.info("Background Blender has no GPU context before 5.0; "
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


def read_color(framebuffer: gpu.types.GPUFrameBuffer, width: int, height: int,
               slot: int = 0) -> np.ndarray:
    """A float colour slot of *framebuffer* as a `(height, width, 4)` array.

    Row 0 is the bottom of the image, matching `Image.pixels`. The result
    is a copy: a `np.frombuffer` view stays backed by the `Buffer`, which
    is freed when this returns.
    """
    buffer = gpu.types.Buffer('FLOAT', width * height * 4)
    with framebuffer.bind():
        framebuffer.read_color(0, 0, width, height, 4, slot, 'FLOAT', data=buffer)
    return np.frombuffer(buffer, dtype=np.float32).reshape(height, width, 4).copy()
