"""Selection masks built from the ops stored on the tree (PS-091).

`outline` turns a lasso outline into the lookup tables a fragment pass
reads, with numpy and no GPU. `raster` runs the passes, caches the masks
by a digest of the ops, and is what tools, the brush stencil and the
overlays call: `get_mask` to build, `peek_mask` from a draw callback.
`session` resolves what the live selection applies to, the active layer,
and keeps one timer that builds its mask and brings what is derived from
it in step. `stencil` clips native strokes to that mask through
Blender's Stencil Mask, and registers its message bus subscriptions and
image editor header note here. `overlay` draws the live selection in the
3D view and the image editor.

The package holds no classes. It registers the stencil's hooks and the
overlay's draw handlers, and gives its GPU objects back on unregister:
Python's own teardown frees them after the GPU context has gone, which
segfaults a background Blender. Import from the submodules directly.
`handlers.node_tree_handlers` drops the cache when a file is read.
"""
from . import overlay, raster, session, stencil


def register() -> None:
    stencil.register()
    overlay.register()


def unregister() -> None:
    # Stop the session's timer before the masks it builds go.
    session.release()
    # Frees its GPU objects while the context exists.
    overlay.unregister()
    # The user's stencil settings come back in every scene.
    stencil.unregister()
    raster.release()
