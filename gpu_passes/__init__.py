"""GPU passes shared by the Epic J tools and the PS-050 image filters.

- `core` has what every pass needs: a check that the `gpu` module can
  draw in this session, saving and restoring the draw state, the quad,
  banded drawing and placeholder sampler of a full-target pass, and a
  readback that behaves the same on 4.2 and 5.x.
- `surface` gives an object's evaluated surface a key based on its
  content, without using the GPU. Caches built from the surface then
  survive events that change nothing.
- `texel_map` rasterises a mesh into UV space, so every texel of a layer
  image knows where it sits on the surface (PS-092).

Import from the submodules directly. The package has no classes. It
registers only so that `unregister` can free its GPU objects. Python's
own teardown would free them after the GPU context is gone, which
crashes a background Blender with a segfault.

`handlers.node_tree_handlers` marks surfaces suspect when geometry may
have changed, and drops all cached data when a file is loaded.
"""
from . import core, surface, texel_map


def register() -> None:
    pass


def unregister() -> None:
    surface.release()
    texel_map.release()
    core.release_unused_sampler()
