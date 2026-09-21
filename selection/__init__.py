"""Selection masks built from the ops stored on the tree (PS-091).

- `outline` turns a lasso outline into lookup tables for the GPU pass.
  It uses numpy only, no GPU.
- `raster` runs the GPU passes and caches masks by a digest of the ops.
  Tools, the brush stencil and the overlays call `get_mask` to build a
  mask, or `peek_mask` from a draw callback.
- `view_raster` draws ops made in the 3D view into the UV-space mask.
- `session` finds what the live selection applies to (the active layer).
  One timer builds its mask and keeps everything derived from it in step.
- `stencil` clips native brush strokes to the mask through Blender's
  Stencil Mask.
- `overlay` draws the live selection in the 3D view and the image
  editor, with the shaders in `overlay_shader`.

The package has no classes. Import from the submodules directly.
`register` adds the stencil's message bus subscriptions and image editor
header note, and the overlay's draw handlers. `unregister` frees the GPU
objects while the GPU context still exists. Python's own teardown would
free them after the context is gone, which segfaults a background
Blender. `handlers.node_tree_handlers` drops the cache when a file is
read.
"""
from . import overlay, raster, session, stencil


def register() -> None:
    stencil.register()
    overlay.register()


def unregister() -> None:
    # Stop the session's timer before the masks it builds are freed.
    session.release()
    # Frees the overlay's GPU objects while the context exists.
    overlay.unregister()
    # Gives the user's stencil settings back in every scene.
    stencil.unregister()
    raster.release()
