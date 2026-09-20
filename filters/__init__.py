# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU image filters and the actions built on them (PS-050).

`core` runs one fragment pass over a layer's image and writes the result
back: the source is the image's own stored values, so a filter that
changes nothing changes no byte. `registry` holds the filters themselves,
`brush_color` resolves what colour a stroke would store, and `actions`
decides whether an action may run on the active layer, limits it to the
selection and commits the result through Blender's image undo.

`composite`, `layer_plan`, `layer_build`, `freshness` and `layer_job` are
the filter *layer* (PS-057): the stack below one drawn into a texture,
filtered, and kept as an image the layer owns, with a check on every
compile of whether those pixels still describe that stack.

The package holds no classes. It registers only so that it has somewhere
to give its GPU objects back: Python's own teardown frees them after the
GPU context has gone, which segfaults a background Blender. Import from
the submodules directly.
"""
from . import blend_glsl, composite, core, layer_job


def register() -> None:
    pass


def unregister() -> None:
    # Before the releases: a refresh in flight is holding textures of its
    # own, and closing its generator is what gives them back.
    layer_job.cancel_all()
    core.release()
    blend_glsl.release()
    composite.release()
