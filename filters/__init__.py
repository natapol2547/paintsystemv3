# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU image filters, and the layer actions and filter layers built on them.

Layer actions (PS-050):

- `core` runs one GPU pass over a layer's image. The pass reads the
  image's own stored values, so a filter that changes nothing changes no
  byte.
- `registry` holds the filters.
- `brush_color` works out the colour a brush stroke would store.
- `actions` checks that an action may run on the active layer, runs its
  passes inside the selection, and writes the result with image undo.

Filter layers (PS-057) are `composite`, `layer_plan`, `layer_build`,
`freshness` and `layer_job`. A filter layer draws the stack below it into
a texture, filters it, and keeps the result as an image it owns. Every
compile checks whether that image still matches the stack. `painter` is
the one kind of filter layer with its own build (PS-053).

The package has no classes. It registers only so that `unregister` can
free its GPU objects. Python's own teardown would free them after the
GPU context is gone, which segfaults a background Blender. Import from
the submodules directly.
"""
from . import blend_glsl, composite, core, layer_job
from .painter import build as painter_build


def register() -> None:
    pass


def unregister() -> None:
    # Cancel first. A refresh in flight holds its own textures, and
    # closing its generator is what frees them.
    layer_job.cancel_all()
    core.release()
    blend_glsl.release()
    composite.release()
    painter_build.release()
