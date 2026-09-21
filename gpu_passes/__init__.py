"""GPU passes shared by the Epic J tools and the PS-050 image filters.

`core` holds what every pass needs: whether the `gpu` module can draw at
all in this session, saving and restoring the draw state, the quad,
banded draw and placeholder sampler of a full-target pass, and a read
back that behaves the same on 4.2 and 5.x. `surface` keys an object's
evaluated surface by its content, without the GPU, so caches built from
it survive events that change nothing.
`texel_map` rasterises a mesh into UV space so every texel of a layer
image knows where it sits on the surface (PS-092).

The package holds no classes, and registers only so that it has somewhere
to give its GPU objects back: Python's own teardown frees them after the
GPU context has gone, which segfaults a background Blender. Import from
the submodules directly. `handlers.node_tree_handlers` marks surfaces
suspect when geometry may have changed, and drops everything when a file
is read.
"""
from . import core, surface, texel_map


def register() -> None:
    pass


def unregister() -> None:
    surface.release()
    texel_map.release()
    core.release_unused_sampler()
