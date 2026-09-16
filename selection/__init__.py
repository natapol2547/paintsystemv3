"""Selection masks built from the ops stored on the tree (PS-091).

`outline` turns a lasso outline into the lookup tables a fragment pass
reads, with numpy and no GPU. `raster` runs the passes, caches the masks
by a digest of the ops, and is what tools, the brush stencil and the
overlays call: `get_mask` to build, `peek_mask` from a draw callback.

The package holds no classes, and registers only so that it has somewhere
to give its GPU objects back: Python's own teardown frees them after the
GPU context has gone, which segfaults a background Blender. Import from
the submodules directly. `handlers.node_tree_handlers` drops the cache
when a file is read.
"""
from . import raster


def register() -> None:
    pass


def unregister() -> None:
    raster.release()
