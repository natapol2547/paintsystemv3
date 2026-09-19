# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU image filters and the actions built on them (PS-050).

`core` runs one fragment pass over a layer's image and writes the result
back: the source is the image's own stored values, so a filter that
changes nothing changes no byte. `registry` holds the filters themselves,
`brush_color` resolves what colour a stroke would store, and `actions`
decides whether an action may run on the active layer, limits it to the
selection and commits the result through Blender's image undo.

The package holds no classes. It registers only so that it has somewhere
to give its GPU objects back: Python's own teardown frees them after the
GPU context has gone, which segfaults a background Blender. Import from
the submodules directly.
"""
from . import core


def register() -> None:
    pass


def unregister() -> None:
    core.release()
